"""Local, one-pair TCP byte relay for controlled co-op delay tests.

Does not decode or retain packet contents. Delays application bytes, not TCP
acknowledgements, retransmissions, or bandwidth. This is not a WAN emulator.
"""
import argparse
from collections import deque
import json
from pathlib import Path
import random
import select
import socket
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--listen-port',type=int,required=True)
    parser.add_argument('--host-port',type=int,required=True)
    parser.add_argument('--seconds',type=int,default=1200)
    args=parser.parse_args()
    if args.output.exists() or not 1<=args.listen_port<=65535 or not 1<=args.host_port<=65535 or args.listen_port==args.host_port:
        parser.error('Use a new output directory and distinct valid ports')
    if not 10<=args.seconds<=1800: parser.error('Duration must be 10..1800 seconds')
    args.output.mkdir(parents=True)
    control=args.output/'control.json'
    control.write_text('{"delay_ms":0,"jitter_ms":0}\n')
    status={'pid':__import__('os').getpid(),'state':'listening','listen_port':args.listen_port,
            'host_port':args.host_port,'segments':[], 'error':None,
            'limitation':'Application byte delay only; TCP acknowledgements remain local; no packet contents retained'}
    started=time.monotonic(); deadline=started+args.seconds
    def publish():
        temp=args.output/'status.tmp'
        temp.write_text(json.dumps(status,indent=2)+'\n')
        try:
            temp.replace(args.output/'status.json')
        except PermissionError:
            # Windows readers may omit FILE_SHARE_DELETE. A status observer
            # must not tear down the live relay; retry on the next publication.
            pass
    listener=socket.socket(); listener.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
    listener.bind(('127.0.0.1',args.listen_port)); listener.listen(1); listener.settimeout(.25)
    publish(); peers=[]
    try:
        while time.monotonic()<deadline:
            try: client,_=listener.accept(); break
            except socket.timeout: continue
        else: raise TimeoutError('No client connected')
        peers.append(client)
        host=socket.create_connection(('127.0.0.1',args.host_port),timeout=5); peers.append(host)
        listener.close()
        for peer in peers:
            peer.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1); peer.setblocking(False)
        queues=[deque(),deque()]; pending=[0,0]; eof=[False,False]; half_closed=[False,False]
        rng=random.Random(0); settings=None; segment=None; next_control=next_publish=0
        status['state']='connected';publish()
        while time.monotonic()<deadline:
            now=time.monotonic()
            if now>=next_control:
                next_control=now+.1
                try:
                    requested=json.loads(control.read_text())
                    if requested.get('stop') is True: status['state']='stopped'; break
                    delay=float(requested['delay_ms']); jitter=float(requested.get('jitter_ms',0))
                    if not 0<=jitter<=delay<=500: raise ValueError('Invalid delay/jitter')
                    if settings!=(delay,jitter):
                        settings=(delay,jitter)
                        segment={'start_seconds':now-started,'delay_ms':delay,'jitter_ms':jitter,
                                 'read_bytes':[0,0],'sent_bytes':[0,0],'chunks':[0,0],
                                 'max_pending_bytes':[0,0], 'first_write_delay_ms':[]}
                        status['segments'].append(segment)
                except (OSError,ValueError,KeyError,TypeError):
                    pass  # A partial editor write does not change active settings.
            if settings is None:
                time.sleep(.002);continue
            readable=[peers[i] for i in range(2) if not eof[i] and pending[i]<1024*1024]
            writable=[peers[1-i] for i in range(2) if queues[i] and queues[i][0][0]<=now]
            if readable or writable:
                ready_read,ready_write,_=select.select(readable,writable,[],.002)
            else:
                time.sleep(.002);ready_read=[];ready_write=[]
            for i in range(2):
                if peers[i] in ready_read:
                    data=peers[i].recv(min(16384,1024*1024-pending[i]))
                    if not data: eof[i]=True
                    else:
                        received=time.monotonic()
                        due=received+(settings[0]+rng.uniform(-settings[1],settings[1]))/1000
                        if queues[i]: due=max(due,queues[i][-1][0])
                        queues[i].append([due,memoryview(data),received,segment,False]);pending[i]+=len(data)
                        segment['read_bytes'][i]+=len(data);segment['chunks'][i]+=1
                        segment['max_pending_bytes'][i]=max(segment['max_pending_bytes'][i],pending[i])
                if peers[1-i] in ready_write and queues[i]:
                    entry=queues[i][0]
                    try: sent=peers[1-i].send(entry[1])
                    except BlockingIOError: continue
                    if not sent: raise ConnectionError('Relay write closed')
                    if not entry[4]:
                        # Bounded timing sample, no payload bytes in reports.
                        samples=entry[3]['first_write_delay_ms']
                        if len(samples)<2048: samples.append((time.monotonic()-entry[2])*1000)
                        entry[4]=True
                    pending[i]-=sent;entry[3]['sent_bytes'][i]+=sent
                    if sent==len(entry[1]): queues[i].popleft()
                    else: entry[1]=entry[1][sent:]
                if eof[i] and not queues[i] and not half_closed[i]:
                    peers[1-i].shutdown(socket.SHUT_WR);half_closed[i]=True
            if all(eof) and not any(queues): status['state']='closed';break
            if now>=next_publish: next_publish=now+1;publish()
        else: status['state']='expired'
    except Exception as exc:
        status['state']='failed';status['error']=str(exc)
        raise
    finally:
        listener.close()
        for peer in peers: peer.close()
        status['elapsed_seconds']=time.monotonic()-started;publish()


if __name__=='__main__': main()
