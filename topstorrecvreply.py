#!/usr/bin/python3
import pika, sys, subprocess, threading, functools
from time import sleep
import actionreply

# The consumer of the 'recvreply' queue (pumped commands, log messages, syncs).  P-DEVELOPMENT note 122:
# it used auto_ack and ran each command inside the pika callback.  A command longer than the AMQP heartbeat (60 s;
# UnixAddUser with a home folder took 160 s) blocked the heartbeats, the broker closed the connection and every message
# already pushed to this client was lost.  Now: one message at a time (prefetch 1), the command runs in a worker thread
# while the connection thread keeps answering heartbeats, and the message is acked only when the command has finished.
# A message that was not acked stays on the broker and comes again after a reconnect (a restart of this script runs it again).

def getlocal(key):
 cmdline='docker exec etcdclient /TopStor/etcdgetlocal.py '+key
 return subprocess.run(cmdline.split(),stdout=subprocess.PIPE).stdout.decode('utf-8').replace('\n','').replace(' ','')

def readids():
 global leader, leaderip, myhost, myip, etcdip
 leader = getlocal('leader')
 leaderip = getlocal('leaderip')
 myhost = getlocal('clusternode')
 myip = getlocal('clusternodeip')
 if leader == myhost:
    etcdip = leaderip
 else:
    etcdip = myip

current = {'body': None, 'thread': None}   # the last message given to a worker

def ack(ch, tag):
 if ch.is_open:
  ch.basic_ack(tag)

def work(conn, ch, tag, body, before=None):
 try:
  if before is not None:
   # the broker gave the message again after a reconnect, while (or after) this process ran it: do not run it twice
   before.join()
  else:
   print(" ".join([leader, leaderip, myhost, myip,etcdip, str(body)]))
   with open("/root/recvreply",'a') as f:
      f.write(" ".join([leader, leaderip, myhost, myip,etcdip, str(body)]))
   actionreply.do(leader, leaderip, myhost, myip,etcdip, body.decode())
 except Exception as e:
  print('recvreply: the message failed', str(body)[:200], e)
 finally:
  try:
   conn.add_callback_threadsafe(functools.partial(ack, ch, tag))
  except Exception as e:
   # the connection is gone: the broker gives the message again on the next connection
   print('recvreply: no ack, connection closed', e)

def callback(ch, method, properties, body):
 before = None
 if method.redelivered and body == current['body'] and current['thread'] is not None:
  before = current['thread']
 t = threading.Thread(target=work, args=(ch.connection, ch, method.delivery_tag, body, before), daemon=True)
 if before is None:
  current['body'], current['thread'] = body, t
 t.start()

readids()
print('myip',myip)
cred = pika.PlainCredentials('rabb_Mezo', 'YousefNadody')
while True:
 try:
  param = pika.ConnectionParameters(myip,5672, '/', cred)
  conn = pika.BlockingConnection(param)
  chann= conn.channel()
  chann.queue_declare(queue='recvreply')   # the same (non-durable) queue the senders and the old consumer use
  chann.basic_qos(prefetch_count=1)
  chann.basic_consume(queue='recvreply', on_message_callback=callback, auto_ack=False)
  chann.start_consuming()
 except KeyboardInterrupt:
  break
 except Exception as e:
  print('recvreply: connection lost, reconnecting', e)
 sleep(3)
 readids()
