"""Local FD/mount proofs with virtual guest roots; no host credential reads."""
import errno,os,pathlib,tempfile,unittest,types
from contextlib import ExitStack
from unittest.mock import patch,Mock
from commander_gym import manual_runtime_qa as qa
from commander_gym.manual_runtime_profile import ManualRuntimeError

BASE=b'1 0 8:1 / / rw - ext4 /dev/vda1 rw\n2 1 0:3 / /run rw - tmpfs tmpfs rw\n'

class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='qa-boundary-test-');self.addCleanup(self.tmp.cleanup)
        self.root=pathlib.Path(self.tmp.name);(self.root/'run').mkdir(mode=0o755)
        self.cred=self.root/'run/credentials';self.overrides={}
    def invoke(self, *, tables=None, open_hook=None):
        original_stat=os.stat;original_fstat=os.fstat;original_open=os.open
        fields=('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
        def metadata(info):
            v={k:getattr(info,k) for k in fields};v.update(st_uid=0,st_gid=0)
            v.update(self.overrides.get(info.st_ino,{}));return types.SimpleNamespace(**v)
        def stat_fn(*a,**k):return metadata(original_stat(*a,**k))
        def fstat_fn(fd):return metadata(original_fstat(fd))
        def opener(*a,**k):
            if open_hook is not None:open_hook(*a,**k)
            return original_open(*a,**k)
        with ExitStack() as stack:
            stack.enter_context(patch.object(qa,'_qa_open_guest_root',side_effect=lambda:original_open(self.root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)))
            stack.enter_context(patch.object(qa,'_qa_read_mountinfo',side_effect=tables or [BASE,BASE]))
            stack.enter_context(patch.object(qa.os,'stat',side_effect=stat_fn))
            stack.enter_context(patch.object(qa.os,'fstat',side_effect=fstat_fn))
            stack.enter_context(patch.object(qa.os,'open',side_effect=opener))
            qa._qa_credentials_boundary()
    def directory(self,mode=0o755):self.cred.mkdir(mode=mode);self.cred.chmod(mode)
    def test_absent_leaf(self):self.invoke()
    def test_empty_safe_modes(self):
        for mode in [0o700,0o750,0o755]:
            with self.subTest(mode=mode):self.directory(mode);self.invoke();self.cred.rmdir()
    def test_unsafe_mode_and_special_bits(self):
        for mode in [0o777,0o775,0o1777,0o4755]:
            with self.subTest(mode=mode):
                self.directory(mode)
                with self.assertRaises(ManualRuntimeError):self.invoke()
                self.cred.rmdir()
    def test_wrong_uid_and_gid(self):
        self.directory();inode=self.cred.stat().st_ino
        for changed in [dict(st_uid=999),dict(st_gid=999)]:
            self.overrides[inode]=changed
            with self.subTest(changed=changed),self.assertRaises(ManualRuntimeError):self.invoke()
    def test_nonempty_and_hidden_files(self):
        self.directory()
        for name in ['payload','.hidden']:
            p=self.cred/name;p.write_bytes(b'not-read')
            with self.subTest(name=name),self.assertRaises(ManualRuntimeError):self.invoke()
            p.unlink()
    def test_empty_service_subdirectory_is_rejected(self):
        self.directory();(self.cred/'unit').mkdir()
        with self.assertRaises(ManualRuntimeError):self.invoke()
    def test_symlink_child_is_rejected(self):
        self.directory();(self.cred/'link').symlink_to(self.root/'missing')
        with self.assertRaises(ManualRuntimeError):self.invoke()
    def test_leaf_symlink_and_regular_file(self):
        self.cred.symlink_to(self.root/'missing')
        with self.assertRaises(ManualRuntimeError):self.invoke()
        self.cred.unlink();self.cred.write_bytes(b'not-read')
        with self.assertRaises(ManualRuntimeError):self.invoke()
    def test_run_symlink_rejected(self):
        (self.root/'run').rmdir();(self.root/'target').mkdir();(self.root/'run').symlink_to(self.root/'target')
        with self.assertRaises(ManualRuntimeError):self.invoke()
    def test_permission_and_io_errors_not_absent(self):
        for number in [errno.EACCES,errno.EIO]:
            def fail(path,*a,**k):
                if path=='credentials':raise OSError(number,'synthetic')
            with self.subTest(number=number),self.assertRaises(ManualRuntimeError):self.invoke(open_hook=fail)
    def test_mount_table_change_is_rejected(self):
        self.directory()
        with self.assertRaises(ManualRuntimeError):self.invoke(tables=[BASE,BASE+b'3 1 0:4 / /tmp rw - tmpfs tmpfs rw\n'])
    def test_insertion_after_first_empty_check(self):
        self.directory();calls=0
        def table():
            nonlocal calls
            calls+=1
            if calls==2:(self.cred/'new').touch()
            return BASE
        with patch.object(qa,'_qa_read_mountinfo',side_effect=table):
            # invoke installs its own table reader, so feed callable effects directly.
            with self.assertRaises(ManualRuntimeError):self.invoke(tables=table)
    def test_insert_remove_changes_directory_identity(self):
        self.directory();calls=0
        def table():
            nonlocal calls
            calls+=1
            if calls==2:
                p=self.cred/'transient';p.touch();p.unlink()
            return BASE
        with self.assertRaises(ManualRuntimeError):self.invoke(tables=table)
    def test_leaf_replacement_rejected(self):
        self.directory();calls=0
        def table():
            nonlocal calls
            calls+=1
            if calls==2:self.cred.rename(self.root/'old');self.directory()
            return BASE
        with self.assertRaises(ManualRuntimeError):self.invoke(tables=table)
    def test_mounts_at_below_or_originating_in_credentials_rejected(self):
        rows=[b'3 2 0:4 / /run/credentials rw - tmpfs tmpfs rw\n',
              b'3 2 0:4 / /run/credentials/unit rw - tmpfs tmpfs rw\n',
              b'3 2 0:4 /run/credentials/unit /elsewhere rw - tmpfs tmpfs rw\n',
              b'3 2 0:4 /run/host/credentials/unit /elsewhere rw - tmpfs tmpfs rw\n',
              b'3 2 0:4 / /elsewhere rw - tmpfs /run/credentials/unit rw\n',
              b'3 2 0:4 / /elsewhere rw - tmpfs tmpfs:/run/host/credentials/unit rw\n']
        for row in rows:
            with self.subTest(row=row),self.assertRaises(ManualRuntimeError):qa._qa_validate_mountinfo(BASE+row)
    def test_every_old_host_share_fstype_rejected(self):
        for fs in ['9p','virtiofs','nfs','nfs4','cifs']:
            with self.subTest(fs=fs),self.assertRaises(ManualRuntimeError):qa._qa_validate_mountinfo(BASE+('3 2 0:4 / /share rw - %s source rw\n'%fs).encode())
    def test_component_prefix_confusion_not_a_credential_mount(self):
        qa._qa_validate_mountinfo(BASE+b'3 2 0:4 / /run/credentials-extra rw - tmpfs tmpfs rw\n')
    def test_escaped_credential_child_rejected(self):
        with self.assertRaises(ManualRuntimeError):qa._qa_validate_mountinfo(BASE+b'3 2 0:4 /run/credentials/unit\\040name /elsewhere rw - tmpfs tmpfs rw\n')
    def test_malformed_mountinfo_rejected(self):
        for raw in [b'',BASE[:-1],BASE+b'bad\n',BASE+b'3 2 0:4 / /tmp rw tmpfs none rw\n',BASE+b'3 2 0:4 / /a\\077b rw - tmpfs none rw\n',BASE+b'3 2 0:4 / /a/../b rw - tmpfs none rw\n',BASE+b'\0',b'x'*(1024*1024+1)]:
            with self.subTest(prefix=raw[:50]),self.assertRaises(ManualRuntimeError):qa._qa_validate_mountinfo(raw)

class OriginalBoundaryTests(unittest.TestCase):
    def check(self, *, marker=b'commander-gym-isolated-qa-v1\n',nics=('lo',),bad_path=None,symlink=False):
        def path(value):
            obj=Mock()
            if value=='/sys/class/net':obj.iterdir.return_value=[types.SimpleNamespace(name=n) for n in nics]
            else:obj.exists.return_value=value==bad_path and not symlink;obj.is_symlink.return_value=value==bad_path and symlink
            return obj
        with patch.object(qa.sys,'platform','linux'),patch.object(qa.os,'geteuid',return_value=0),patch.object(qa,'root_bytes',return_value=marker),patch.object(qa,'Path',side_effect=path),patch.object(qa,'_qa_credentials_boundary') as boundary:
            qa.guest_boundary();return boundary.call_count
    def test_otherwise_valid_guest_calls_narrow_boundary(self):self.assertEqual(self.check(),1)
    def test_original_marker_and_nic_gates_retained(self):
        for kwargs in [dict(marker=b'wrong'),dict(nics=('lo','eth0'))]:
            with self.subTest(kwargs=kwargs),self.assertRaises(ManualRuntimeError):self.check(**kwargs)
    def test_other_production_paths_and_dangling_symlinks_retained(self):
        for path in ['/etc/argentum-luna/operator-policy.json','/etc/argentum-play/qualifications','/var/lib/commander-gym/runs/native']:
            for symlink in [False,True]:
                with self.subTest(path=path,symlink=symlink),self.assertRaises(ManualRuntimeError):self.check(bad_path=path,symlink=symlink)

if __name__=='__main__':unittest.main()
