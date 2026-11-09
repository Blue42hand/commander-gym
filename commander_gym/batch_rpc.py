"""Bounded panel client for the separate UID-authenticated batch broker."""
import json
import socket

class BatchRPCError(Exception):
    def __init__(self,status,message): self.status=status;super().__init__(message)

def call(config,actor,operation,*,batch_id=None,data=None,key=None):
    path=config.get('batch_socket')
    if not path:raise BatchRPCError(503,'Batch supervisor is not configured')
    request={'actor':actor,'operation':operation,'batch_id':batch_id,'data':data or {},'key':key}
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
            sock.settimeout(5);sock.connect(path)
            sock.sendall(json.dumps(request).encode()+b'\n')
            with sock.makefile('rb') as reader: raw=reader.readline(2*1024*1024+1)
        if len(raw)>2*1024*1024:raise ValueError()
        result=json.loads(raw)
    except (OSError,ValueError):raise BatchRPCError(503,'Batch supervisor unavailable; no command acknowledgement received. Retain the command key when retrying.') from None
    if result['status']!=200:raise BatchRPCError(result['status'],result['error'])
    return result['value']
