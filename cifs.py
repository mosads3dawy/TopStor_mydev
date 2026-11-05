#!/usr/bin/python3
import sys, os, subprocess, datetime
from logqueue import queuethis, initqueue
from etcdgetpy import etcdget as get
from sendhost import sendhost
from time import sleep


def refuse(why, *what):
    # fail closed: start nothing, remove nothing, touch no interface; the volume stays dirty and the next volume check retries
    print('cifs.py: share not started:', why, *what)
    try:
        with open('/root/cifsrun.log','a') as flog:
            flog.write(str(datetime.datetime.now())+' refused: '+why+' '+' '.join(str(x) for x in what)+'\n')
    except OSError:
        pass

def create(leader, leaderip, myhost, myhostip, etcdip, pool, name, ipaddr, ipsubnet, vtype,*args):
    # A failed read of the leader etcd gave '_1' for leader/myhostip/etcdip (VolumeCheck.py) and an empty volume list here:
    # the share files were removed, cifs.sh got shifted arguments (empty mounts) and re-activated the cluster interface on
    # every try (QC test 22 step 18, QSD5.315).  Never act on such values.
    for argname, argval in (('leader',leader), ('leaderip',leaderip), ('myhost',myhost), ('myhostip',myhostip),
                            ('etcdip',etcdip), ('pool',pool), ('name',name), ('ipaddr',ipaddr), ('ipsubnet',ipsubnet),
                            ('vtype',vtype)):
        if str(argval).strip() in ('', '_1', 'not reachable'):
            refuse('bad argument', argname, repr(argval))
            return
    volsip = get(etcdip,'volume','/'+ipaddr+'/', strict=True)
    if 'not reachable' in str(volsip):
        refuse('etcd not readable (volumes)', etcdip, ipaddr)
        return
    volsip = [ x for x in volsip if 'active' in str(x) ]
    nodesip = get(etcdip, 'Active','/'+ipaddr+'/', strict=True)
    if 'not reachable' in str(nodesip):
        refuse('etcd not readable (Active)', etcdip, ipaddr)
        return
    notsametype = [ x for x in volsip if vtype not in str(x) ]
    if (len(nodesip) > 0 and 'Active' in str(nodesip))or len(notsametype) > 0:
        print('ipaddr',ipaddr)
        print('nodesip',len(nodesip), nodesip)
        print('nodtsametype',len(notsametype), notsametype)
        print(' the ip address is in use ')
        return
    # nothing of this ip and type to serve: start nothing and keep the current share files (they were removed first)
    if len([ x for x in volsip if x not in notsametype ]) == 0:
        refuse('no active volume of this ip and type in etcd', etcdip, ipaddr, vtype)
        return
    resname = vtype+'-'+ipaddr
    cmdline='rm -rf /TopStordata/tempsmb.'+ipaddr
    subprocess.run(cmdline.split(),stdout=subprocess.PIPE)  
    cmdline='rm -rf /TopStordata/smb.'+ipaddr
    subprocess.run(cmdline.split(),stdout=subprocess.PIPE)  
    mounts =''
    # right after a take over this runs while the pool is still being imported: the volume's dataset is not mounted and
    # /<pool>/smb.<volume> is not readable yet. Reading it then (the error was skipped) gave an empty smb config and a
    # share container without any share, and nothing retried. Wait for the dataset; if it never comes, start nothing:
    # the volume stays dirty and the next volume check starts it again.
    for vol in volsip:
        if vol in notsametype:
           continue
        leftvol = vol[0].split('/')[4]
        for attempt in range(90):
            mounted = subprocess.run(['zfs','list','-H','-o','mounted',pool+'/'+leftvol],stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout.decode().strip()
            if mounted == 'yes' and os.path.exists('/'+pool+'/smb.'+leftvol):
                break
            sleep(2)
        else:
            print('pool volume not mounted yet, not starting the share', pool, leftvol)
            return
    for vol in volsip:
        if vol in notsametype:
           continue
        leftvol = vol[0].split('/')[4]
        mounts += '-v/'+pool+'/'+leftvol+':/'+pool+'/'+leftvol+':rw'
        with open('/TopStordata/tempsmb.'+ipaddr,'a') as fip:
            try:
                with open('/'+pool+'/smb.'+leftvol, 'r') as fvol:
                    fip.write(fvol.read())
            except:
               continue
    if mounts == '':
        refuse('no volume to mount', ipaddr, vtype)   # never start a share without its volumes
        return
    cmdline = 'cp /TopStordata/tempsmb.'+ipaddr+' /TopStordata/smb.'+ipaddr
    subprocess.run(cmdline.split(),stdout=subprocess.PIPE)  
    if '_' not in vtype:
        cmdline = 'cp /TopStor/VolumeCIFSupdate.sh /etc/'
        subprocess.run(cmdline.split(),stdout=subprocess.PIPE)  
    print('hihihihi')
    print('cmd: '+'/TopStor/cifs.sh '+resname+' '+mounts+' '+ipaddr+' '+ipsubnet+' '+vtype+' '+" ".join(args))
    print('end of cmd')
    # an argument list, not a split string: an empty field used to vanish and shift every later argument of cifs.sh
    cmdline = ['/TopStor/cifs.sh', resname, mounts, ipaddr, ipsubnet, vtype] + list(args)
    # the share container can die within seconds of its start (docker --rm then removes it and its logs): after the second
    # take over of a test the container was created, found "not running" 2 s later and nobody started it again -- the
    # password loop below then ran for minutes against a container that did not exist, and VolumeCheck (which waits for
    # this script) could not repair anything.  Start it again until it stays up; keep the output of every try.
    running = False
    for attempt in range(5):
        res = subprocess.run(cmdline,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        with open('/root/cifsrun.log','a') as flog:
            flog.write(str(datetime.datetime.now())+' '+resname+' try '+str(attempt)+' rc '+str(res.returncode)+'\n'+res.stdout.decode()[-1500:]+'\n')
        if res.returncode == 2 and b'cifs.sh: bad arguments' in res.stdout:
            # cifs.sh refused its arguments before touching anything: a retry would refuse them again
            print('cifs.sh refused the arguments, see /root/cifsrun.log', resname)
            return
        sleep(8)
        up = subprocess.run(['docker','ps','-q','-f','name=^'+resname+'$'],stdout=subprocess.PIPE).stdout.decode().strip()
        if up:
            running = True
            break
    if not running:
        print('share container did not stay up after 5 tries, see /root/cifsrun.log', resname)
        return
    if '_' not in vtype:
        # the script that sets a user's samba password inside the share container; UnixAddUser_sync copies it, but the
        # node that created the users (the leader runs UnixAddUser) never ran that, so a share created later found no file
        subprocess.run(['cp','-f','/TopStor/smbuserfix.sh','/etc/smbuserfix.sh'],stdout=subprocess.PIPE)
        users=get(etcdip,'usershash','--prefix')
        users=[x for x in users if 'admin' not in x[0] ]
        for user in users:
            username = user[0].split('/')[1]
            cmdline = '/TopStor/decthis.sh '+username+' '+user[1]
            passwd = subprocess.run(cmdline.split(),stdout=subprocess.PIPE).stdout.decode().split('_result')[1]
            # the share container is started in the background: its samba database may not answer yet, so the password is
            # set again until the user is in it (up to ~3 min; two shares created together take longer)
            for attempt in range(75):
                if not subprocess.run(['docker','ps','-q','-f','name=^'+resname+'$'],stdout=subprocess.PIPE).stdout.decode().strip():
                    print('share container is gone, password of', username, 'not set')
                    break
                cmdline = 'docker exec '+resname+' /hostetc/smbuserfix.sh x '+username+' '+passwd
                subprocess.run(cmdline.split(),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                check = subprocess.run(['docker','exec',resname,'pdbedit','-L','-u',username],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                if check.returncode == 0 and username.encode() in check.stdout:
                    break
                sleep(2)
            
    print(mounts)
    return
    #if len(checkipaddr1) != 0 or len :

 

if __name__=='__main__':
 leader = sys.argv[1]
 leaderip = sys.argv[2]
 myhost = sys.argv[3]
 myhostip = sys.argv[4]
 etcdip = sys.argv[5]
 pool = sys.argv[6]
 name = sys.argv[7]
 ipaddr = sys.argv[8]
 ipsubnet = sys.argv[9]
 vtype = sys.argv[10]
 initqueue(leaderip, myhost)
 with open('/root/cifspytmp','w') as f:
  f.write(str(sys.argv))
 create(leader, leaderip, myhost, myhostip, etcdip, pool, name, ipaddr, ipsubnet, vtype,*sys.argv[11:])
