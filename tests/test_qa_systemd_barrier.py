"""Adversarial metadata-only proofs; no mounts, credential contents or host reads.

The actual matched module is imported from root's new staged source checkout.
Synthetic FD graph is explicit; it is not guest custody or runtime qualification.
"""
import errno,os,stat,types,unittest,json,traceback
from contextlib import ExitStack
from unittest.mock import patch
from commander_gym import manual_runtime_qa as qa
from commander_gym.manual_runtime_profile import ManualRuntimeError

BASE=b'1 0 8:1 / / rw - ext4 /dev/vda rw\n2 1 0:29 / /run rw,nosuid,nodev - tmpfs tmpfs rw,size=1024k,mode=755\n'
BARRIER=b'3 2 0:29 /systemd/inaccessible/dir /run/credentials ro,nosuid,nodev,noexec,relatime - tmpfs tmpfs rw,size=1024k,mode=755\n'
TABLE=BASE+BARRIER
FIELDS=('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
def metadata(inode,mode=0o755,device=None,size=80,links=3):
    return types.SimpleNamespace(st_dev=os.makedev(0,29) if device is None else device,
        st_ino=inode,st_mode=stat.S_IFDIR|mode,st_uid=0,st_gid=0,st_nlink=links,
        st_size=size,st_mtime_ns=100,st_ctime_ns=100)

class SystemdBarrierTests(unittest.TestCase):
    def setUp(self):
        self.info={100:metadata(1,device=os.makedev(8,1)),101:metadata(2),102:metadata(3),103:metadata(4,mode=0o555),
                   104:metadata(8,mode=0,size=40,links=2),105:metadata(8,mode=0,size=40,links=2)}
        self.edges={(100,'run'):101,(101,'systemd'):102,(102,'inaccessible'):103,(103,'dir'):104,(101,'credentials'):105}
        self.opened=[];self.closed=[];self.tables=TABLE;self.fault=None;self.stat_hook=None
    def opener(self,name,flags,*,dir_fd=None):
        self.assertTrue(flags&os.O_PATH);self.assertTrue(flags&os.O_NOFOLLOW)
        self.assertTrue(flags&os.O_DIRECTORY);self.assertTrue(flags&os.O_CLOEXEC)
        self.assertEqual(flags&os.O_ACCMODE,os.O_RDONLY) # O_PATH is metadata-only.
        if name=='/' and dir_fd is None:return 100
        if self.fault is not None:self.fault(name,dir_fd)
        key=(dir_fd,name)
        if key not in self.edges:raise FileNotFoundError(name)
        fd=self.edges[key];self.opened.append(fd);return fd
    def named(self,name,*,dir_fd,follow_symlinks):
        self.assertFalse(follow_symlinks)
        if self.stat_hook is not None:self.stat_hook(name,dir_fd)
        if (dir_fd,name) not in self.edges:raise FileNotFoundError(name)
        return self.info[self.edges[(dir_fd,name)]]
    def invoke(self, *, direct=False,environment=None):
        with ExitStack() as stack:
            stack.enter_context(patch.object(qa,'_qa_open_guest_root',return_value=100))
            stack.enter_context(patch.object(qa,'_qa_read_mountinfo',side_effect=self.tables if callable(self.tables) else lambda:self.tables))
            stack.enter_context(patch.object(qa.os,'open',side_effect=self.opener))
            stack.enter_context(patch.object(qa.os,'fstat',side_effect=lambda fd:self.info[fd]))
            stack.enter_context(patch.object(qa.os,'stat',side_effect=self.named))
            stack.enter_context(patch.object(qa.os,'close',side_effect=self.closed.append))
            stack.enter_context(patch.object(qa.os,'listdir',side_effect=AssertionError('Never list inaccessible/credential directory')))
            stack.enter_context(patch.object(qa.os,'read',side_effect=AssertionError('Never read credential bytes')))
            stack.enter_context(patch.dict(qa.os.environ,environment or {},clear=True))
            if direct:qa._qa_systemd_barrier_boundary(self.tables)
            else:qa._qa_credentials_boundary()
    def test_failure_metadata_survives_default_traceback_without_content_reads(self):
        self.tables=BASE+BARRIER.replace(b'ro,nosuid,nodev,noexec,relatime',b'ro,relatime')
        with self.assertRaises(ManualRuntimeError) as caught:self.invoke()
        error=caught.exception
        self.assertEqual(str(error),'qa_systemd_barrier_mount')
        note=error.__notes__[0]
        self.assertLessEqual(len(note),2048)
        data=json.loads(note.split('=',1)[1])
        self.assertEqual(data['missingRequiredOptions'],['nodev','noexec','nosuid'])
        self.assertEqual(data['checks'],dict(root=True,fstype=True,source=True,requiredOptions=False,forbiddenOptionsAbsent=True,allowedOptionsOnly=True))
        self.assertEqual(data['barrier']['id'],3)
        self.assertEqual(data['run']['id'],2)
        self.assertIn(note,''.join(traceback.TracebackException.from_exception(error).format()))
        self.assertEqual(self.opened,[]) # Failed predicate runs before metadata FDs.
    def test_failure_note_does_not_echo_unrecognized_fields_or_options(self):
        marker='synthetic-private-never-echo'
        self.tables=BASE+BARRIER.replace(b'tmpfs tmpfs',('tmpfs '+marker).encode()).replace(b'ro,nosuid,nodev,noexec,relatime',('ro,'+marker).encode())
        with self.assertRaises(ManualRuntimeError) as caught:self.invoke()
        note=caught.exception.__notes__[0]
        self.assertNotIn(marker,note)
        data=json.loads(note.split('=',1)[1])
        self.assertFalse(data['checks']['source'])
        self.assertFalse(data['checks']['allowedOptionsOnly'])
        self.assertEqual(data['barrier']['unknownOptionCount'],1)
    def test_failure_note_uses_same_parsed_table_without_reread(self):
        bad=BASE+BARRIER.replace(b'ro,nosuid,nodev,noexec,relatime',b'ro')
        with patch.object(qa,'_qa_read_mountinfo',side_effect=AssertionError('No diagnostic reread')),patch.object(qa.os,'open',side_effect=AssertionError('No diagnostic file open')):
            with self.assertRaises(ManualRuntimeError) as caught:qa._qa_systemd_barrier_boundary(bad)
        self.assertIn('qa_systemd_barrier_metadata=',caught.exception.__notes__[0])
    def test_authentic_empty_tmpfs_sentinel_passes_metadata_only(self):
        self.invoke();self.assertEqual(self.closed,[105,104,103,102,101,100])
    def test_readonly_systemd_parent_matches_real_systemd_metadata(self):
        self.assertEqual(stat.S_IMODE(self.info[103].st_mode),0o555)
        self.invoke();self.assertEqual(self.closed,[105,104,103,102,101,100])
    def test_readonly_parent_exception_is_not_general(self):
        for fd in (100,101,102):
            old=self.info[fd].st_mode;self.info[fd].st_mode=stat.S_IFDIR|0o555
            with self.subTest(fd=fd),self.assertRaises(ManualRuntimeError):self.invoke()
            self.info[fd].st_mode=old
        self.info[106]=metadata(10,mode=0o555);self.edges[(101,'host')]=106
        with self.assertRaises(ManualRuntimeError):self.invoke()
    def test_systemd_readonly_parent_still_requires_type_ownership_and_safe_mode(self):
        for field,bad in (('st_uid',999),('st_gid',999),('st_mode',stat.S_IFREG|0o555),('st_mode',stat.S_IFLNK|0o555),('st_mode',stat.S_IFDIR|0o575),('st_mode',stat.S_IFDIR|0o557),('st_mode',stat.S_IFDIR|0o777),('st_mode',stat.S_IFDIR|0o1555)):
            old=getattr(self.info[103],field);setattr(self.info[103],field,bad)
            with self.subTest(field=field,bad=bad),self.assertRaises(ManualRuntimeError):self.invoke()
            setattr(self.info[103],field,old)
    def test_prior_safe_systemd_parent_modes_remain_supported(self):
        for mode in (0o700,0o750,0o755):
            self.info[103].st_mode=stat.S_IFDIR|mode
            with self.subTest(mode=mode):self.invoke()
    def test_readonly_systemd_parent_identity_mutation_is_rejected(self):
        calls=0
        def table():
            nonlocal calls
            calls+=1
            if calls==2:self.info[103].st_ctime_ns+=1
            return TABLE
        self.tables=table
        with self.assertRaises(ManualRuntimeError):self.invoke()
    def test_default_validator_always_rejects_barrier_without_fd_proof(self):
        with self.assertRaises(ManualRuntimeError):qa._qa_validate_mountinfo(TABLE)
        with self.assertRaises(TypeError):qa._qa_validate_mountinfo(TABLE,approved=True)
    def test_unmounted_default_parser_still_accepts_original_base(self):qa._qa_validate_mountinfo(BASE)
    def test_wrong_filesystem_or_source_root_or_source_name_reject(self):
        for row in (BARRIER.replace(b' - tmpfs ',b' - ext4 '),BARRIER.replace(b'/systemd/inaccessible/dir',b'/fake/empty'),BARRIER.replace(b'tmpfs tmpfs',b'tmpfs /run/credentials/unit')):
            self.tables=BASE+row
            with self.subTest(row=row),self.assertRaises(ManualRuntimeError):self.invoke()
    def test_wrong_parent_device_or_run_root_reject(self):
        for table in (TABLE.replace(b'3 2 0:29',b'3 1 0:29'),TABLE.replace(b'3 2 0:29',b'3 2 0:30'),TABLE.replace(b'2 1 0:29 / /run',b'2 1 0:29 /alias /run')):
            self.tables=table
            with self.subTest(table=table),self.assertRaises(ManualRuntimeError):self.invoke()
    def test_required_mount_flags_all_required_no_writable_counterparts(self):
        good=b'ro,nosuid,nodev,noexec,relatime'
        for bad in (b'rw,nosuid,nodev,noexec',b'ro,nodev,noexec',b'ro,nosuid,noexec',b'ro,nosuid,nodev',b'ro,nosuid,nodev,noexec,exec',b'ro,nosuid,nodev,noexec,unknown'):
            self.tables=BASE+BARRIER.replace(good,bad)
            with self.subTest(bad=bad),self.assertRaises(ManualRuntimeError):self.invoke()
    def test_duplicate_target_or_id_and_noncanonical_id_reject(self):
        for table in (TABLE+BARRIER.replace(b'3 2',b'4 2'),TABLE+BARRIER,TABLE.replace(b'3 2',b'03 2'),TABLE.replace(b'3 2',b'3 02'),TABLE.replace(b'0:29 /systemd',b'0:029 /systemd')):
            self.tables=table
            with self.subTest(table=table),self.assertRaises(ManualRuntimeError):self.invoke()
    def test_credential_descendants_other_roots_and_sources_reject(self):
        for row in (b'4 2 0:29 / /run/credentials/unit rw - tmpfs tmpfs rw\n',b'4 2 0:29 / /run/host/credentials rw - tmpfs tmpfs rw\n',b'4 2 0:29 /run/credentials/unit /alias ro - tmpfs tmpfs rw\n',b'4 2 0:29 / /alias ro - tmpfs tmpfs:/run/host/credentials/x rw\n'):
            self.tables=TABLE+row
            with self.subTest(row=row),self.assertRaises(ManualRuntimeError):self.invoke()
    def test_canonical_source_overmount_alias_rejects(self):
        for point in ('/run/systemd','/run/systemd/inaccessible','/run/systemd/inaccessible/dir','/run/systemd/inaccessible/dir/child'):
            self.tables=TABLE+('4 2 0:29 / %s ro - tmpfs tmpfs rw\n'%point).encode()
            with self.subTest(point=point),self.assertRaises(ManualRuntimeError):self.invoke()
    def test_unrelated_noncredential_protecthome_barrier_keeps_baseline_semantics(self):
        self.tables=TABLE+b'4 1 0:29 /systemd/inaccessible/dir /home ro,nosuid,nodev,noexec - tmpfs tmpfs rw\n'
        self.invoke()
    def test_late_host_share_rejected_before_exception(self):
        for fs in ('9p','virtiofs','nfs','nfs4','cifs'):
            self.tables=TABLE+('4 2 0:40 / /share rw - %s host rw\n'%fs).encode()
            with self.subTest(fs=fs),self.assertRaises(ManualRuntimeError):self.invoke()
    def test_populated_regular_symlink_or_child_directory_metadata_rejects(self):
        for size,links in ((60,2),(80,2),(60,3),(40,3),(0,2),(4096,2)):
            for fd in (104,105):self.info[fd].st_size=size;self.info[fd].st_nlink=links
            with self.subTest(size=size,links=links),self.assertRaises(ManualRuntimeError):self.invoke()
    def test_wrong_source_target_inode_device_mode_and_owner_reject(self):
        for fd,field,bad in ((105,'st_ino',9),(105,'st_dev',os.makedev(0,30)),(104,'st_mode',stat.S_IFDIR|0o700),(105,'st_mode',stat.S_IFREG),(104,'st_uid',999),(105,'st_gid',999)):
            old=getattr(self.info[fd],field);setattr(self.info[fd],field,bad)
            with self.subTest(fd=fd,field=field),self.assertRaises(ManualRuntimeError):self.invoke()
            setattr(self.info[fd],field,old)
    def test_parent_modes_not_broadened_and_crossdevice_chain_rejects(self):
        for fd,field,bad in ((100,'st_mode',stat.S_IFDIR|0o555),(101,'st_mode',stat.S_IFDIR|0o777),(102,'st_uid',999),(103,'st_gid',999),(102,'st_dev',os.makedev(0,40))):
            old=getattr(self.info[fd],field);setattr(self.info[fd],field,bad)
            with self.subTest(fd=fd,field=field),self.assertRaises(ManualRuntimeError):self.invoke()
            setattr(self.info[fd],field,old)
    def test_canonical_symlink_and_io_errors_are_not_absence(self):
        for err in (errno.ELOOP,errno.ENOTDIR,errno.EACCES,errno.EIO):
            def fault(name,fd):
                if name=='dir':raise OSError(err,'synthetic no-content fault')
            self.fault=fault
            with self.subTest(errno=err),self.assertRaises(ManualRuntimeError):self.invoke()
    def test_named_source_swap_rejects(self):
        def swap(name,fd):
            if name=='dir':self.edges[(fd,name)]=105;self.info[105].st_ino=9
        self.stat_hook=swap
        with self.assertRaises(ManualRuntimeError):self.invoke()
    def test_credential_environment_keys_reject_without_value_read(self):
        for key in ('CREDENTIALS_DIRECTORY','ENCRYPTED_CREDENTIALS_DIRECTORY','SYSTEMD_CREDENTIALS'):
            with self.subTest(key=key),self.assertRaises(ManualRuntimeError):self.invoke(environment={key:'synthetic-never-read'})
    def test_other_unmounted_credential_root_rejects(self):
        self.info[106]=metadata(10);self.edges[(101,'host')]=106;self.edges[(106,'credentials')]=104
        with self.assertRaises(ManualRuntimeError):self.invoke()
    def test_other_absent_credential_root_is_stable(self):
        self.info[106]=metadata(10);self.edges[(101,'host')]=106;self.invoke()
    def test_changed_mounttable_or_late_share_rejects(self):
        calls=0
        def table():
            nonlocal calls
            calls+=1;return TABLE if calls==1 else TABLE+b'4 2 0:40 / /share rw - nfs host rw\n'
        self.tables=table
        with self.assertRaises(ManualRuntimeError):self.invoke()
    def test_leaf_mutation_during_first_or_final_table_read_rejects(self):
        for mutate_at in (2,3):
            calls=0
            def table():
                nonlocal calls
                calls+=1
                if calls==mutate_at:self.info[104].st_size=60;self.info[105].st_size=60
                return TABLE
            self.info[104].st_size=self.info[105].st_size=40;self.tables=table
            with self.subTest(call=mutate_at),self.assertRaises(ManualRuntimeError):self.invoke()
    def test_insert_remove_stamp_change_not_hidden_by_size40(self):
        calls=0
        def table():
            nonlocal calls
            calls+=1
            if calls==2:self.info[104].st_ctime_ns+=1;self.info[105].st_ctime_ns+=1
            return TABLE
        self.tables=table
        with self.assertRaises(ManualRuntimeError):self.invoke()
    def test_malformed_optional_escape_and_row_reject(self):
        for raw in (TABLE[:-1],TABLE.replace(b'3 2 0:29',b'3 2 bad'),TABLE.replace(b'ro,nosuid',b'ro,,nosuid'),TABLE.replace(b' - tmpfs tmpfs',b' unknown:1 - tmpfs tmpfs'),TABLE+b'bad\n',TABLE.replace(b'/systemd/inaccessible/dir',b'/systemd/../dir')):
            self.tables=raw
            with self.subTest(raw=raw),self.assertRaises(ManualRuntimeError):self.invoke()

    def test_arbitrary_unmounted_mode000_remains_rejected(self):
        with self.assertRaises(ManualRuntimeError):qa._qa_safe_directory(metadata(8,mode=0,size=40,links=2))
    def test_non_ascii_ids_reject_in_boundary_parser(self):
        self.tables=TABLE.replace(b'3 2 0:29','３ 2 0:29'.encode())
        with self.assertRaises(ManualRuntimeError):self.invoke()

if __name__=='__main__':unittest.main()
