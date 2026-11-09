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


def summary(config,actor):
    if not config.get('batch_socket'):return {'state':'Unavailable','running':None,'scope':'Managed batch runtime only'}
    import time
    try:
        value=call(config,actor,'list')
        healthy=value['health']['supervisor']=='Available' and value['total']<=30
        active=[a for b in value['batches'] for a in b['attempts'] if a['state'] in ('Starting','Running','Stopping')]
        known=healthy and all(a.get('observation') and time.time()-a['observation']['observed_unix']<=20 for a in active)
        running=sum(len(a['observation']['live_games']) for a in active) if known else None
        return {'state':'Fresh' if known else 'Unavailable or stale','running':running,
                'queued_batches':sum(b['state']=='Queued' for b in value['batches']),
                'scope':'Managed batch runtime only; interactive games are outside this source'}
    except (BatchRPCError,KeyError,TypeError):return {'state':'Unavailable','running':None,'scope':'Managed batch runtime only'}
