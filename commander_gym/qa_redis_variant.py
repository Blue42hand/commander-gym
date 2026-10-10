"""Separate nondeployable Redis QA contract; no installer, authority or test runner.

Only selected root-sealed role entries execute these specs. Operator owns service
memory/DAC/closure review. Redis stays alive for native-only interruption.
"""
from pathlib import Path
import re
from .manual_runtime_profile import ManualRuntimeError, decode, protected_bytes, relative_path, require

PROFILE = 'manual-luna-redis-qa-v1'
LOCK = 'qa-redis-lock.json'
CONFIG = b'''bind 127.0.0.1
port 26379
protected-mode yes
daemonize no
databases 1
maxclients 16
maxmemory 16mb
maxmemory-policy noeviction
save ""
appendonly no
logfile ""
'''


def validate_settings(profile):
    require(profile.get('purpose') == 'qualification', 'qa_redis_never_production')
    row = profile.get('redisQa')
    require(type(row) is dict and set(row) == {'runId', 'uid', 'memoryMaxBytes', 'snapshotBytes', 'aggregateSnapshotBytes'}, 'qa_redis_fields')
    require(type(row['runId']) is str and re.fullmatch('[a-f0-9]{32}', row['runId']), 'qa_redis_run')
    require(type(row['uid']) is int and row['uid'] > 0 and row['uid'] not in profile.get('identities', {}).values(), 'qa_redis_identity')
    require(all(type(row[k]) is int and row[k] == v for k, v in
                {'memoryMaxBytes':64*1024**2, 'snapshotBytes':8*1024**2, 'aggregateSnapshotBytes':16*1024**2}.items()), 'qa_redis_bounds')


def role_uid(profile, role):
    if role == 'redis':
        require(profile.get('profile') == PROFILE, 'qa_redis_role_requires_variant')
        validate_settings(profile)
        return profile['redisQa']['uid']
    return profile['identities']['nativeUid' if role in ('native', 'recorder') else role+'Uid']


def dependency_additions(profile):
    """Read exact separately sealed closure lock; no ambient binary discovery."""
    require(profile.get('profile') == PROFILE, 'qa_redis_variant_required')
    artifact = profile['artifacts']['dependencies']
    require(LOCK in artifact['files'], 'qa_redis_lock_missing')
    lock = decode(protected_bytes(Path(artifact['root'])/LOCK, uid=artifact['uid'], max_bytes=256*1024))
    require(set(lock) == {'schemaVersion', 'program', 'config', 'files'} and type(lock['schemaVersion']) is int
            and lock['schemaVersion'] == 1 and type(lock['files']) is dict and 1 <= len(lock['files']) <= 64, 'qa_redis_lock_fields')
    for name, sha in lock['files'].items():
        relative_path(name)
        require(name.startswith('qa-redis/') and type(sha) is str and re.fullmatch('[a-f0-9]{64}', sha)
                and artifact['files'].get(name) == sha, 'qa_redis_closure_seal')
    for key in ('program', 'config'):
        require(type(lock[key]) is str and lock[key] in lock['files'], 'qa_redis_program_config')
    require(lock['program'] == 'qa-redis/redis-server' and lock['config'] == 'qa-redis/redis.conf', 'qa_redis_fixed_entries')
    raw = protected_bytes(Path(artifact['root'])/lock['config'], uid=artifact['uid'], max_bytes=4096)
    require(raw == CONFIG, 'qa_redis_config_changed')
    return {LOCK:artifact['files'][LOCK], **lock['files']}


def native_overrides(profile):
    validate_settings(profile)
    return ('--native.qa.redis-persistence-enabled=true', '--native.qa.callback-initially-paused=true',
            '--cache.redis.enabled=true', '--cache.redis.host=127.0.0.1', '--cache.redis.port=26379',
            '--cache.redis.ttl-minutes=30', '--cache.redis.key-prefix=qa:'+profile['redisQa']['runId']+':')


def redis_spec(runtime):
    from .manual_runtime_launch import LaunchSpec
    profile = runtime.profile
    from .manual_runtime_profile import validate_profile
    validate_profile(profile); dependency_additions(profile)
    root = Path(profile['artifacts']['dependencies']['root'])
    return LaunchSpec((str(root/'qa-redis/redis-server'), str(root/'qa-redis/redis.conf')),
                     {'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8'})
