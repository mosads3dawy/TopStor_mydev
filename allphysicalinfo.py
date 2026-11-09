#!/usr/bin/python3
from logqueue import queuethis
from etcdgetpy import etcdget as get
from ast import literal_eval as mtuple
from raidrank import getraidrank
import subprocess, copy
from levelthis import levelthis
import sys

hostip = ''
def getsnapperiods(voldict):
 global hostip
 periodsdict = dict()
 snapperiods = get(hostip,'Snapperiod','--prefix') 
 snapperiods = [ x for x in snapperiods if 'hosttrend' not in str(x) ]
 if len(snapperiods) < 1 or snapperiods[0] == '_1':
    return periodsdict, voldict
 print('########################################################################3333333333333')
 print(voldict)
 print('snappreiods',snapperiods)
 with open('/TopStor/tempfile','w') as f:
    f.write(str(snapperiods))
 print('########################################################################3333333333333')
 for per in snapperiods:
  leftper = per[0].split('/')
  rightper = per[1].split('/')
  vol = leftper[3]
  if vol in voldict:
   voldict[vol]['snapperiod'].append(leftper[4])
   periodsdict[leftper[4]] = dict()
   periodsdict[leftper[4]]['host']=voldict[vol]['host']
   periodsdict[leftper[4]]['pool']=voldict[vol]['pool']
   periodsdict[leftper[4]]['volume']=vol
   periodsdict[leftper[4]]['periodtype']=leftper[1]
   periodsdict[leftper[4]]['id']= leftper[4]
   periodsdict[leftper[4]]['receiver']=leftper[5]
   if 'Minutely' in leftper[1]: 
    periodsdict[leftper[4]]['keep']=rightper[4].split('.')[1]
    periodsdict[leftper[4]]['every']=rightper[4].split('.')[2]
   elif 'Hourly' in leftper[1]: 
    periodsdict[leftper[4]]['keep']=rightper[4].split('.')[1]
    periodsdict[leftper[4]]['sminute']=rightper[4].split('.')[2]
    periodsdict[leftper[4]]['every']=rightper[4].split('.')[3]
   elif 'Weekly' in leftper[1]: 
    periodsdict[leftper[4]]['keep']=rightper[3].split('.')[1]
    hr=rightper[3].split('.')[2]
    ampm = 'AM'
    if int(hr) > 12 :
     hr = int(hr) - 12
     ampm = 'PM'
    mint=rightper[3].split('.')[3]
    periodsdict[leftper[4]]['stime']=str(hr)+':'+str(mint)+' '+ampm
    periodsdict[leftper[4]]['every']=rightper[3].split('.')[4].split('%')[0]
 return (periodsdict,voldict)

def getall(leadip, alldsks='0'):
 global hostip
 hostip=leadip
 if alldsks == '0':
  alldsks = get(hostip,'host','current')
 hostsdict = dict()
 poolsdict = dict()
 raidsdict = dict()
 volumesdict = dict()
 disksdict = dict()
 freedisks = dict()
 busydisks = dict()
 snapshotsdict = dict()
 snapperiodsdict = dict()
 allvols = []
 availability = get(hostip,'balance','--prefix')
 vols = get(hostip,'volumes','--prefix')
 for vol in vols:
  voldict = dict()
  if len(vol[1].split('/')) < 7:
   continue
  voldict = {'name': vol[1].split('/')[1], 'pool': vol[1].split('/')[0], 'groups': '', 'ipaddress': '', 'Subnet': '', 'prot': '', 'fullname': '', 'host': '', 'creation': '', 'time': '', 'used': 0, 'quota': 0, 'usedbysnapshots': 0, 'refcompressratio': '1.0x', 'runtime': '', 'snapperiod': [], 'snapshots': []}
  if 'CIFS' in vol[0].split('/')[1] or 'HOME' in vol[0].split('/')[1] :
   voldict['groups'] = vol[1].split('/')[4]
   if 'DOMAIN' in voldict['groups']:
    voldict['groups']= 'DOMAIN'
    voldict['type']='DOMAIN'
    voldict['ipaddress'] = vol[1].split('/')[7] 
   else:
    voldict['type']='WorkGroup'
    voldict['ipaddress'] = vol[1].split('/')[7] 
   voldict['Subnet'] = vol[1].split('/')[8]
   voldict['prot'] = vol[0].split('/')[1]
   volumesdict[voldict['name']] = voldict.copy()
  elif vol[0].split('/')[1] == 'NFS':
   voldict['groups'] = vol[1].split('/')[8]
   voldict['ipaddress'] = vol[1].split('/')[9] 
   voldict['Subnet'] = vol[1].split('/')[10]
   voldict['prot'] = 'NFS'
   volumesdict[voldict['name']] = voldict.copy()
  elif vol[0].split('/')[1] == 'ISCSI':
   voldict['groups'] = 'Everyone'
   voldict['ipaddress'] = vol[1].split('/')[2] 
   voldict['Subnet'] = vol[1].split('/')[3]
   voldict['portalport'] = vol[1].split('/')[4]
   voldict['initiators'] = vol[1].split('/')[5]
   voldict['chapuser'] = vol[1].split('/')[6]
   voldict['chappas'] = vol[1].split('/')[7]
   voldict['prot'] = 'ISCSI'
   volumesdict[voldict['name']] = voldict.copy()
  if len(voldict['ipaddress']) < 5:
        voldict['ipaddress'] = '256.256.256.256';
        voldict['Subnet'] = '24'
 
 for alldsk in alldsks:
  if alldsk == '_1':
   continue
  host = alldsk[0].split('/')[1]
  pools = mtuple(alldsk[1])
  hostpools = []
  thehostip = get(hostip,'ActivePartners/'+host)[0]
  hostsdict[host] = {'name': host,'ipaddress': thehostip, 'pools': hostpools }
  for pool in pools:
   pool['used'] = levelthis(pool['used'])
   pool['available'] = levelthis(pool['available'])
   pool['alloc'] = levelthis(pool['alloc'])
   pool['empty'] = levelthis(pool['empty'])
   pool['size'] = levelthis(pool['size'])
   hostpools.append(pool['name'])
   thepool = pool.copy()
   thepool.pop('raidlist',None)
   thepool.pop('volumes',None)
   poolraids = []
   poolsdict[pool['name']] = dict()
   poolsdict[pool['name']] = thepool.copy() 
   poolsdict[pool['name']]['raids'] = poolraids
   if pool['name'] in str(availability):
    poolsdict[pool['name']]['Availability'] = 'Availability'
   else:
    poolsdict[pool['name']]['Availability'] = 'None'
   for raid in pool['raidlist']:
    if 'free' not in raid['name']:
     raidname = raid['name']+'_'+pool['name']
    else:
      raidname = raid['name']
    poolraids.append(raidname)
    theraid = raid.copy()
    raiddisks = []
    raidsdict[raidname] = dict()
    raidsdict[raidname] = theraid.copy()
    if raidname != 'free' and len(raidsdict[raidname]['disklist']) > 0:
     raidsdict[raidname] = getraidrank(raidsdict[raidname],raidsdict[raidname]['disklist'][0],raidsdict[raidname]['disklist'][0])
    theraid.pop('disklist',None)
    raidsdict[raidname]['disks'] = raiddisks
    raidsdict[raidname]['name'] = raidname 
    for disk in raid['disklist']:
     disk['size'] = levelthis(disk['size'])
     if 'free' in raid['name']:
      if disk['name'] not in busydisks:
       freedisks[disk['name']] = disk.copy()
       disksdict[disk['name']] = disk.copy()
       raiddisks.append(disk['name'])
     else:
      busydisks[disk['name']] = disk.copy()
      disksdict[disk['name']] = disk.copy()
      raiddisks.append(disk['name'])
      if disk['name'] in freedisks:
       if disk['name'] in raidsdict['free']['disks']:
        raidsdict['free']['disks'].remove(disk['name'])
   poolvolumes = []
   poolsdict[pool['name']]['volumes'] = poolvolumes
   for volume in pool['volumes']:
    volume['used'] = levelthis(volume['used'])
    volume['available'] = levelthis(volume['available'])
    volume['referenced'] = levelthis(volume['referenced'],'M')
    if volume['prot'] == 'ISCSI':
     volume['quota'] = volume['used'] 
    else:
     volume['quota'] = levelthis(volume['quota'])
    volume['usedbysnapshots'] = levelthis(volume['usedbysnapshots'],'M')
    poolvolumes.append(volume['name'])
    thevolume = volume.copy()
    #echo /pace/etcdput.py volumes/CIFS/$myhost/$DG/$name $DG/$name/no/yes/$writev/administrator/yes >> /root/volchange
    thevolume.pop('snapshots',None)
    volumesnapshots = []
    volumesnapperiods = []
    for snapshot in volume['snapshots']:
     snapshot['used'] = levelthis(snapshot['used'])
     volumesnapshots.append(snapshot['name'])
     snapshotsdict[snapshot['name']] = snapshot.copy() 
    if 'snapperiods' in volume:
     thevolume.pop('snapperiod',None)
     for snapperiod in volume['snapperiod']:
      volumesnapperiods.append(snapperiod['name'])
      snapperiodsdict[snapperiod['name']] = snapperiod.copy() 
    if volume['name'] not in volumesdict:
     volumesdict[volume['name']] = dict()
     volumesdict[volume['name']] = thevolume.copy()
    else:
     volumesdict[volume['name']].update(thevolume) 
    volumesdict[volume['name']]['snapshots'] = volumesnapshots
    volumesdict[volume['name']]['snapperiod'] = volumesnapperiods
 toremove=[]
 for volume in volumesdict:
  if volume not in str(poolsdict):
   toremove.append(volume)
 for volume in toremove:
  volumesdict.pop(volume)
 snapperiodsdict = dict()
 snapperiodsdict, volumesdict = getsnapperiods(volumesdict) 
 for disk in disksdict:
  diskraids = []
  diskpool = disksdict[disk]['pool']
  if 'pdhcp' in diskpool:
   disksdict[disk]['raid'] = disksdict[disk]['raid']+'_'+diskpool
 if 'free' in raidsdict:
  if len(raidsdict['free']['disks']) == 0:
   raidsdict.pop('free',None) 
 '''
 print('#############')
 print('hosts',hostsdict)
 print('#############')
 print('pools',poolsdict)
 print('#############')
 print('raids',raidsdict)
 print('#############')
 print('disks',disksdict)
 print('#############')
 print('volumes',volumesdict)
 print('#############')
 print('snapshots',snapshotsdict)
 print('#############')
 print('snapperiods',snapperiodsdict) 
 '''
 #print('disks',disksdict)
 print('pools',poolsdict)
 return {'hosts':hostsdict, 'pools':poolsdict, 'raids':raidsdict, 'disks':disksdict, 'volumes':volumesdict, 'snapshots':snapshotsdict, 'snapperiods':snapperiodsdict}

def initallphy(ipadd):
    global hostip
    hostip = ipadd 
if __name__=='__main__':
 hostip = sys.argv[1]
 alldsks = get(hostip,'host','current')
 getall(hostip, alldsks)
