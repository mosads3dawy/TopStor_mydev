#!/usr/bin/python3
import subprocess,sys, os
import json
from time import sleep
from checkleader import checkleader

dev = 'enp0s8'
os.environ['ETCDCTL_API']= '3'

def etcdctl(etcd, key,prefix, timeout=2):
 cmdline=['etcdctl','--user=root:YN-Password_123','--endpoints=http://'+etcd+':2379','get',key,prefix]
 cmdline=['etcdctl','--endpoints=http://'+etcd+':2379','get',key,prefix]
 try:
    result=subprocess.run(cmdline,stdout=subprocess.PIPE, timeout=timeout)
    return result
 except:
    return '_1'

def etcdget(etcd, key, prefix='', *, strict=False):   # keyword-only, like the pace copy: old calls are unchanged
 # A failed read (etcdctl rc != 0, etcd down, timeout) prints nothing and looked exactly like an absent key ('_1') or an
 # empty prefix ([]).  cifs.py took such a failed read of the leader for "no volume on this ip" and started a share without
 # any volume (and bounced the cluster interface).  strict=True returns ['not reachable'] on a failed read so the caller
 # can stop; without strict the result (and the 2 s timeout) is the same as before.
 if strict and prefix not in ('', '--prefix'):
  result = etcdctl(etcd, key, '--prefix', timeout=30)   # the filtered form below only uses this --prefix read
 elif strict:
  result = etcdctl(etcd, key, prefix, timeout=30)       # etcdctl has its own 5 s command timeout; 30 s only guards a hang
 else:
  result = etcdctl(etcd, key,prefix)
 if strict and (isinstance(result, str) or result.returncode != 0):
  return ['not reachable']
 z=[]
 try:
  if(prefix =='--prefix'):
   mylist=(lambda o: (o[:-1] if o.endswith('\n') else o).split('\n') if o else [])(str(result.stdout.decode()))   # an empty value stays an (empty) line: collapsing it shifted every later key/value pair
   zipped=zip(mylist[0::2],mylist[1::2])
   for x in zipped:
    z.append(x) 
  elif(prefix == ''):
   if len(str(result.stdout).split(key)) > 2 :	
    z.append(key.join(str(result.stdout).split(key)[1:])[2:][:-3])
   else:
    z.append((str(result.stdout).split(key)[1][2:][:-3]))
  else:
   if not strict:
    result = etcdctl(etcd,key,'--prefix')
   mylist=(lambda o: (o[:-1] if o.endswith('\n') else o).split('\n') if o else [])(str(result.stdout.decode()))   # an empty value stays an (empty) line: collapsing it shifted every later key/value pair
   zipped=zip(mylist[0::2],mylist[1::2])
   for x in zipped:
    if prefix in str(x):
     z.append(x)
   if(len(z) == 0):
     z.append('_1')
 except:
  z.append('_1')
 return z
if __name__=='__main__':
 etcdget(*sys.argv[1:])
