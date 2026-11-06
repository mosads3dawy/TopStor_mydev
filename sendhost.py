#!/usr/bin/python3
import pika
from ast import literal_eval as mtuple

def sendhost(host, req, que, frmhst, port=5672):
 with open('/root/sendhostparam','a') as f:
  f.write(host+' '+str(req)+' '+str(que)+' '+str(frmhst)+'\n')
# Check if the file needs to be pruned
 with open('/root/sendhostparam', 'r') as f:
        lines = f.readlines()

 if len(lines) > 30:
        # Keep only the last 30 lines
        with open('/root/sendhostparam', 'w') as f:
            f.writelines(lines[-30:])
# creds=pika.PlainCredentials('rabb_'+frmhst,'YousefNadody')
 creds=pika.PlainCredentials('rabb_Mezo','YousefNadody')
 # Short timeouts and one attempt: an unreachable host (a known/ peer that is down) must fail in seconds, not block the caller
 # (logmsg.sendlog -> fapi.py login() returned HTTP 500 while a peer was off; P-DEVELOPMENT note 118).
 param=pika.ConnectionParameters(host, port, '/', creds, socket_timeout=3, blocked_connection_timeout=3,
                                 connection_attempts=1, stack_timeout=5)
 conn=pika.BlockingConnection(param)
 chann=conn.channel()
 chann.basic_publish(exchange='',routing_key=que, body=str(req))
 conn.close()
if __name__ == "__main__":
 import sys
 if len(sys.argv) < 2:
    import os
    host='10.11.11.250' if (os.path.exists('/.dockerenv') or os.path.isdir('/sys/class/net/eth10')) else '10.11.11.100'
    req = {'req': 'Pumpthis', 'reply': ['/TopStor/VolumeCreateCIFS', '10.11.11.100', 'pdhcp1055322164', 'nf5', '1G', 'Everyone', '10.11.11.34', '24', 'active', 'admin', 'dhcp152953', 'admin']}
    que='recvreply'
    frmhst='dhcp152953'
    sendhost(host, req, que, frmhst)
 else:
    sendhost(*sys.argv[1:])
 
