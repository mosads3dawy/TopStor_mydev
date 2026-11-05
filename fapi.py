#!/usr/bin/python3
import time as _startclock; _FAPI_T0 = _startclock.time()      # for the start-up log at the bottom: when the interpreter started
import time
# sample-data mode (pzfs UI work): /TopStordata/mockapi exists -> serve mockfapi.py instead of the real API (rm it to switch off)
import os as _os
if _os.path.exists('/TopStordata/mockapi'):
    import mockfapi; mockfapi.run(); raise SystemExit
import flask, os, Evacuate, subprocess, Joincluster, sys, re
from getversions import getversions
from functools import wraps
from copy import deepcopy
from flask import request, jsonify, render_template, redirect, url_for, g, send_file
import re, ipaddress, traceback
import Hostsconfig
from Hostconfig import config
from allphysicalinfo import getall, initallphy
from uservalid import check_new_user_coded, logname
from UnixChkUser import setlogin
import sqlite3
from etcdget2 import etcdgetjson
from etcdgetlocalpy import etcdget  as get
from etcddellocal import etcddel  as dels 
from etcdput import etcdput as put
from sendhost import sendhost
from socket import gethostname as hostname
from getlogs import getlogs, onedaylog
from fapistats import allvolstats, cpuperf
from datetime import datetime
from getallraids import newraids
from fastselect import selectdisks
from raid10 import selectraid10
from raid5060 import selectraid50, selectraid60, raidwidthof
from secrets import token_hex
from time import time as timestamp
import logmsg
from collectNodeConfig import getConfig, downloadConfig
import zipfile
from time import sleep
import socket
import json
import threading

_cmdctx = threading.local()
_orig_popen_init = subprocess.Popen.__init__

def _cmdaudit_popen_init(self, args, *a, **kw):
 try:
  cmds = getattr(_cmdctx, 'commands', None)
  if cmds is not None:
   cmds.append(args if isinstance(args, str) else ' '.join(str(x) for x in args))
 except:
  pass
 return _orig_popen_init(self, args, *a, **kw)

subprocess.Popen.__init__ = _cmdaudit_popen_init

getalltimestamp = 0
os.environ['ETCDCTL_API'] = '3'
loggedusers = dict() 
alldsks = []
allpools = 0
allgroups = []
allvolumes = []
allusers = []
readyhosts = []
activehosts = []
losthosts = []
possiblehosts = []
pooldict = dict()
app = flask.Flask(__name__)
app.config["DEBUG"] = True
logcatalog = ''
with open('/TopStor/msgsglobal.txt') as f:
 logcatalog = f.read().split('\n')
logdict = dict()
for log in logcatalog:
 msgcode= log.split(':')[0]
 logdict[msgcode] = log.replace(msgcode+':','').split(' ')
allinfo = 0

def is_valid_ip(ip):
    try:
        ipaddress.ip_address(ip)
        return 0 
    except ValueError:
        return 10 


def is_unique_ip(ip, vtype='NZ#@A' ):
        global leaderip
        if not ip or not str(ip).strip():
            return 0
        allvols = get('vol', '--prefix')
        allvols = [x for x in allvols if vtype not in str(x)]
        allvols = str(allvols)
        allnodes= get('Active','--prefix')
        allnodes = str(allnodes)
        allpartners= get('Partner','--prefix')
        allpartners = str(allpartners)
        allips = allvols+'/'+leaderip+'/'+allnodes+'/'+allpartners
        ip_pattern = rf'\b{re.escape(ip)}\b' 
        if bool(re.search(ip_pattern,allips)):
                    print("Invalid IP")
                    return 100 
        print("Valid IP")
        return 0 

# Every user is a member of the group Everyone, always: the front end neither shows nor edits it, so the group lists
# it sends never contain it.  Every command that sets the groups of a user goes through this function.
EVERYONE = 'Everyone'
def with_everyone(groupstr):
        names = [g for g in str(groupstr).split(',') if g and g != 'NoGroup']
        if EVERYONE not in names:
                names.append(EVERYONE)
        return ','.join(names)

# Everyone as the group of a share means all users (every user is a member of it): when it is among the groups the
# others add nothing, so the share gets Everyone alone.
def only_everyone(groups):
        names = [g for g in str(groups).split(',') if g]
        return EVERYONE if EVERYONE in names else groups

# Which volumes may share the IP address a new or changed volume asks for.  is_unique_ip() leaves out the volumes whose etcd
# key holds this text: a CIFS share may share an address only with the CIFS shares of the same context (the workgroup shares
# volumes/CIFS/..., the shares of a domain volumes/CIFS_<domain>/...: one share container serves one workgroup or one domain),
# homes with homes, iSCSI with iSCSI; NFS shares share no address at all.
def ip_context(kind, domain=''):
        if kind == 'CIFS':
                return 'volumes/CIFS/'
        if kind == 'CIFSdom':
                return 'volumes/CIFS_'+str(domain)+'/'
        if kind.startswith('CIFS_'):
                return 'volumes/'+kind+'/'
        if 'NFS' in kind:
                return 'ANYthing'
        return kind

def has_privilege(priv, user):
        # same rule as /TopStor/privthis.sh: admin and system may do everything, any other user needs the privilege flag '<priv>-true' in
        # usersinfo/<user>.  Read from etcd here: this code runs inside the 'flask' container, where privthis.sh cannot work (/bin/sh is dash
        # and there is no docker CLI), so shelling out answered 'false' for every non-admin user, also for one that HAS the privilege.
        if user in ('admin', 'system'):
                return True
        try:
                info = str(get('usersinfo/'+str(user))[0])
                return re.search(r'(^|/)'+re.escape(str(priv))+r'-true(/|$)', info) is not None
        except Exception:
                return False

def is_unique_name(name):
        global leaderip
        allvols = str(get('vol', '--prefix'))
        alluser= str(get('user','--prefix'))
        allgrp= str(get('group','--prefix'))
        allnames = allvols+'/'+alluser+'/'+allgrp
        name_pattern = rf'\b{re.escape(name)}\b' 
        if bool(re.search(name_pattern,allnames)):
                    print("Invalid Name")
                    return 1000 
        print("Valid Name")
        return 0 



def getalltime(renew='no'):
 global allinfo,alldsks, getalltimestamp, leaderip
 #if (getalltimestamp+30) < timestamp() or renew == 'yes':
 if (getalltimestamp+1) < timestamp() or renew == 'yes':
  alldsks = deepcopy(get('host','current'))
  allinfo = deepcopy(getall(leaderip, alldsks))
  getalltimestamp = timestamp()
 return

# ---------------------------------------------------------------------------------------------------------------------------
# API keys (QSD5.308): named credentials for scripts and applications.  A key is NOT a system user (no unix user, group, home,
# no usersinfo/usersigroup entry, no console login); its name is the user name of the audit log and of the notifications.
# The key  qsk.<name>.<secret>  is passed as 'token'.  etcd (leader, synced to every node as the whole-etcd sync 'apikeys'):
#    apikeys/<name> = <access>/<status>/<created>/<createdby>/<lastused>/<revoked>/<salt>/<hash>     ('-' = empty)
# only a salted HMAC of the secret is stored; the key itself is shown once, on create/regenerate.
import hmac as _hmac, hashlib as _hashlib, secrets as _secrets
from etcdgetpy import etcdget as _getleader
from etcddel import etcddel as _delleader

APIKEY_NAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{2,31}$')
APIKEY_FAIL_MAX = 10                 # failed key attempts from one source IP ...
APIKEY_FAIL_WINDOW = 300             # ... within this many seconds lock that IP out ...
APIKEY_LOCKOUT = 300                 # ... for this many seconds
APIKEY_LASTUSED_EVERY = 60           # lastused is written at most once a minute per key
_apikey_fails = {}                   # ip -> [timestamps of failures]
_apikey_locked = {}                  # ip -> locked until
_apikey_lastwrite = {}               # name -> time of the last lastused write

# the key may never call these (key management, users, groups, privileges, joining, software, login session)
APIKEY_NEVER = ('/api/v1/users/uploadUsers', '/api/v1/groups/groupchange', '/api/v1/users/userchange', '/api/v1/users/userdel',
                '/api/v1/groups/groupdel', '/api/v1/groups/UnixAddgroup', '/api/v1/users/UnixAddUser', '/api/v1/users/usersauth',
                '/api/v1/user/changepass', '/api/v1/tenant/adduser', '/api/v1/hosts/joincluster', '/api/v1/software/update',
                '/api/v1/software/localFileUpdate', '/api/v1/software/setversion', '/api/v1/software/apply',
                '/api/v1/login/renewtoken', '/api/v1/logout', '/api/v1/replication/addpartner')
# calls that change nothing: a 'read' key may call only these (every other call is a change call)
APIKEY_READ = ('/api/v1/volumes/connections', '/api/v1/software/versions', '/api/v1/hosts/allinfo', '/api/v1/pools/dgsinfo',
               '/api/v1/volumes/stats', '/api/v1/volumes/volumelist', '/api/v1/tenants/tenantinfo', '/api/v1/volumes/poolsinfo',
               '/api/v1/volumes/snapshots/snapshotsinfo', '/api/v1/volumes/CIFS/volumesinfo', '/api/v1/volumes/ISCSI/volumesinfo',
               '/api/v1/volumes/NFS/volumesinfo', '/api/v1/volumes/HOME/volumesinfo', '/api/v1/volumes/volumesinfo',
               '/api/v1/pools/poolsinfo', '/api/v1/info/onedaylog', '/api/v1/info/logs', '/api/v1/info/commandlog',
               '/api/v1/info/cversion', '/api/v1/info/notification', '/api/v1/info/summary', '/api/v1/volumes/grouplist',
               '/api/v1/groups/grouplist', '/api/v1/users/userauths', '/api/v1/partners/partnerlist',
               '/api/v1/partners/freeport', '/api/v1/users/userlist', '/api/v1/groups/userlist', '/api/v1/users/grouplist',
               '/api/v1/hosts/getConfig', '/api/v1/hosts/getAllConfig', '/api/v1/login/test')

def _apikey_hash(salt, secret):
 return _hmac.new(salt.encode(), secret.encode(), _hashlib.sha256).hexdigest()

def _apikey_record(raw):
 # etcd value -> dict (None when it is not a key record)
 f = str(raw).split('/')
 if len(f) != 8:
  return None
 return {'access': f[0], 'status': f[1], 'created': f[2], 'createdby': f[3], 'lastused': f[4], 'revoked': f[5], 'salt': f[6], 'hash': f[7]}

def _apikey_value(rec):
 return '/'.join(str(rec[k]) or '-' for k in ('access','status','created','createdby','lastused','revoked','salt','hash'))

def apikeys_all():
 # name -> record, read from the leader's etcd (a revoke is effective at once, on whichever node the call arrives)
 keys = {}
 try:
  rows = _getleader(leaderip, 'apikeys/', '--prefix')
 except Exception:
  rows = get('apikeys/', '--prefix')
 for row in rows:
  if not isinstance(row, (tuple, list)) or len(row) < 2:
   continue
  rec = _apikey_record(row[1])
  if rec:
   keys[str(row[0]).split('/', 1)[1]] = rec
 return keys

def apikey_sync(name):
 # whole-etcd sync 'apikeys' (checksyncs.py): every node copies apikeys/* from the leader
 global myhost
 _delleader(leaderip, 'sync', 'apikeys_')
 stampv = 'apikeys_'+str(timestamp())
 put(leaderip, 'sync/apikeys/Add_'+name+'/request', stampv)
 put(leaderip, 'sync/apikeys/Add_'+name+'/request/'+myhost, stampv)

def apikey_put(name, rec):
 put(leaderip, 'apikeys/'+name, _apikey_value(rec))

def apikey_clientip():
 try:
  return request.remote_addr or '-'
 except Exception:
  return '-'

def apikey_refused(ip, name, why):
 now = timestamp()
 fails = [t for t in _apikey_fails.get(ip, []) if t > now-APIKEY_FAIL_WINDOW]
 fails.append(now)
 _apikey_fails[ip] = fails
 try:
  if len(fails) <= APIKEY_FAIL_MAX:
   logmsg.sendlog('ApiKeyfa0', 'warning', 'system', str(name)[:40] or '-', ip)
  if len(fails) == APIKEY_FAIL_MAX:
   logmsg.sendlog('ApiKeyfa1', 'warning', 'system', ip)
 except Exception:
  pass
 if len(fails) >= APIKEY_FAIL_MAX:
  _apikey_locked[ip] = now+APIKEY_LOCKOUT
 return {'response': 'baduser'}

def apikey_authenticate(data):
 # returns (None, data prepared) when the key is good, else (reply, None)
 ip = apikey_clientip()
 now = timestamp()
 if _apikey_locked.get(ip, 0) > now:
  return {'response': 'baduser'}, None
 token = str(data.get('token', ''))
 parts = token.split('.', 2)
 if len(parts) != 3 or parts[0] != 'qsk' or not APIKEY_NAME_RE.match(parts[1]) or len(parts[2]) < 32:
  return apikey_refused(ip, parts[1] if len(parts) > 1 else '-', 'malformed'), None
 name, secret = parts[1], parts[2]
 rec = apikeys_all().get(name)
 if not rec:
  return apikey_refused(ip, name, 'unknown'), None
 if not _hmac.compare_digest(_apikey_hash(rec['salt'], secret), rec['hash']) or rec['status'] != 'active':
  return apikey_refused(ip, name, 'bad'), None
 path = request.path
 if path.startswith('/api/v1/apikeys/') or path in APIKEY_NEVER:
  return {'response': 'error', 'message': 'not allowed for an API key'}, None
 if rec['access'] != 'write' and path not in APIKEY_READ:
  return {'response': 'error', 'message': 'key is read only'}, None
 _apikey_fails.pop(ip, None)
 if _apikey_lastwrite.get(name, 0)+APIKEY_LASTUSED_EVERY < now:
  _apikey_lastwrite[name] = now
  rec['lastused'] = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
  try:
   apikey_put(name, rec)
  except Exception:
   pass
 data['user'] = name
 data['response'] = name
 data['keyaccess'] = rec['access']
 return None, data

def apikey_manager(data):
 # None when the caller (a logged in user, never a key) may manage keys, else the reply
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 if data.get('keyaccess') or not has_privilege('UserPrivilegesch', data['user']):
  return {'response': 'error', 'message': 'not authorized'}
 return None

def apikey_nameclash(name):
 if name in ('admin', 'system', 'root'):
  return True
 try:
  if name in [u['name'].replace('usersinfo/', '') for u in etcdgetjson(leaderip, 'usersinfo', '--prefix')]:
   return True
  if name in [str(g[0]) for g in getgroups()]:
   return True
 except Exception as e:
  print('apikey_nameclash:', e)
 return False

def apikey_newsecret(rec):
 secret = _secrets.token_urlsafe(30)
 rec['salt'] = _secrets.token_hex(16)
 rec['hash'] = _apikey_hash(rec['salt'], secret)
 return secret

def apikey_access(value, default='read'):
 value = str(value or default).lower()
 return value if value in ('read', 'write') else None

def login_required(f):
 global loggedusers
 @wraps(f)
 def decorated_function(*args, **kwargs):
  global loggedusers
  data = request.args.to_dict()
  if 'disks[]' in data:
   data['disks'] = request.args.getlist('disks[]')
   del data['disks[]']
  if 'cache[]' in data:
   data['cache'] = request.args.getlist('cache[]')
   del data['cache[]']
  if 'cache_disks[]' in data:
   data['cache_disks'] = request.args.getlist('cache_disks[]')
   del data['cache_disks[]']


  for dat in data:
   if isinstance(data[dat], str):
    data[dat] = data[dat].replace(' ','')
  data['response'] = 'baduser'
  if str(data.get('token', '')).startswith('qsk.'):
   reply, keydata = apikey_authenticate(data)
   if reply is not None:
    return reply
   return f(keydata)
  if data['token'] in loggedusers:
   if loggedusers[data['token']]['timestamp'] > timestamp():
    data['user'] = loggedusers[data['token']]['user']
    data['response'] = data['user'] 
    return f(data)
   else:
    try:
        logmsg.sendlog('Lognsa0','warning','system',loggedusers[data['token']]['user'])
        return f({'response':'baduser'})
    except:
        return f({'response':'baduser'})
  else:
   try:
    logmsg.sendlog('Lognno0','warning','system1',data['token'])
    return f({'response':'baduser'})
   except:
    return f({'response':'baduser'})
 return decorated_function

COMMAND_LOG_PATH = '/TopStordata/cmdaudit.log'

def record_command(data, endpoint, method, commands=None):
 try:
  user = data.get('user')
  if not user:
   return
  excluded = ('response', 'user', 'token', 'keyaccess')
  params = dict()
  for k in data:
   if k in excluded:
    continue
   if 'pass' in k.lower():
    continue
   params[k] = data[k]
  entry = {
   'date': datetime.now().strftime('%m/%d/%Y'),
   'time': datetime.now().strftime('%H:%M:%S'),
   'ts': timestamp(),
   'user': user,
   'source': 'apikey' if data.get('keyaccess') else 'login',
   'host': hostname(),
   'endpoint': endpoint,
   'method': method,
   'params': params,
   'commands': commands or [],
  }
  with open(COMMAND_LOG_PATH, 'a') as fh:
   fh.write(json.dumps(entry)+'\n')
 except:
  pass

def get_command_log(user=None, limit=200):
 if not os.path.exists(COMMAND_LOG_PATH):
  return []
 with open(COMMAND_LOG_PATH) as fh:
  rows = fh.readlines()[-5000:]
 entries = []
 for row in reversed(rows):
  row = row.strip()
  if not row:
   continue
  try:
   entry = json.loads(row)
  except:
   continue
  if user and entry.get('user') != user:
   continue
  entries.append(entry)
  if len(entries) >= limit:
   break
 return entries

def audit_command(f):
 @wraps(f)
 def decorated_function(data, *args, **kwargs):
  _cmdctx.commands = []
  try:
   result = f(data, *args, **kwargs)
  finally:
   cmds = getattr(_cmdctx, 'commands', [])
   _cmdctx.commands = None
   try:
    record_command(data, request.path, request.method, cmds)
   except:
    pass
  return result
 return decorated_function

@app.route('/api/v1/apikeys/list', methods=['GET','POST'])
@login_required
@audit_command
def apikeys_list(data):
 refused = apikey_manager(data)
 if refused:
  return refused
 allkeys = []
 for name, rec in sorted(apikeys_all().items()):
  allkeys.append({'name': name, 'access': rec['access'], 'status': rec['status'], 'created': rec['created'],
                  'createdby': rec['createdby'], 'lastused': rec['lastused'], 'revoked': rec['revoked']})
 return {'response': 'Ok', 'allkeys': allkeys}

@app.route('/api/v1/apikeys/create', methods=['GET','POST'])
@login_required
@audit_command
def apikeys_create(data):
 refused = apikey_manager(data)
 if refused:
  return refused
 name = str(data.get('name', ''))
 access = apikey_access(data.get('access'))
 if not APIKEY_NAME_RE.match(name):
  return {'response': 'error', 'message': 'name must be 3-32 characters: letters, digits, _ or -, starting with a letter'}
 if access is None:
  return {'response': 'error', 'message': 'access must be read or write'}
 if name in apikeys_all():
  return {'response': 'error', 'message': 'a key with this name already exists'}
 if apikey_nameclash(name):
  return {'response': 'error', 'message': 'the name is already used by a user or a group'}
 rec = {'access': access, 'status': 'active', 'created': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'createdby': data['user'], 'lastused': '-', 'revoked': '-', 'salt': '', 'hash': ''}
 secret = apikey_newsecret(rec)
 apikey_put(name, rec)
 apikey_sync(name)
 logmsg.sendlog('ApiKey1000', 'info', data['user'], name)
 return {'response': 'Ok', 'name': name, 'access': access, 'key': 'qsk.'+name+'.'+secret}

@app.route('/api/v1/apikeys/regenerate', methods=['GET','POST'])
@login_required
@audit_command
def apikeys_regenerate(data):
 refused = apikey_manager(data)
 if refused:
  return refused
 name = str(data.get('name', ''))
 rec = apikeys_all().get(name)
 if not rec:
  return {'response': 'error', 'message': 'no such key'}
 access = apikey_access(data.get('access'), rec['access'])
 if access is None:
  return {'response': 'error', 'message': 'access must be read or write'}
 rec.update({'access': access, 'status': 'active', 'revoked': '-', 'lastused': '-',
             'created': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'), 'createdby': data['user']})
 secret = apikey_newsecret(rec)
 apikey_put(name, rec)
 apikey_sync(name)
 logmsg.sendlog('ApiKey1001', 'info', data['user'], name)
 return {'response': 'Ok', 'name': name, 'access': access, 'key': 'qsk.'+name+'.'+secret}

@app.route('/api/v1/apikeys/revoke', methods=['GET','POST'])
@login_required
@audit_command
def apikeys_revoke(data):
 refused = apikey_manager(data)
 if refused:
  return refused
 name = str(data.get('name', ''))
 rec = apikeys_all().get(name)
 if not rec:
  return {'response': 'error', 'message': 'no such key'}
 rec['status'] = 'revoked'
 rec['revoked'] = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
 apikey_put(name, rec)
 apikey_sync(name)
 logmsg.sendlog('ApiKey1002', 'info', data['user'], name)
 return {'response': 'Ok', 'name': name, 'status': 'revoked'}

@app.route('/api/v1/apikeys/delete', methods=['GET','POST'])
@login_required
@audit_command
def apikeys_delete(data):
 refused = apikey_manager(data)
 if refused:
  return refused
 name = str(data.get('name', ''))
 if name not in apikeys_all():
  return {'response': 'error', 'message': 'no such key'}
 _delleader(leaderip, 'apikeys/'+name)
 apikey_sync(name)
 logmsg.sendlog('ApiKey1003', 'info', data['user'], name)
 return {'response': 'Ok', 'name': name}


@app.route('/api/v1/users/uploadUsers', methods=['GET','POST'])
@login_required
@audit_command
def uploadUsers(data):
    global allgroups, leaderip, myhost
    if 'baduser' in data['response']:
      return {'response': 'baduser'}
    uploaded_file = request.files['file']
    if uploaded_file.filename != '':
      dirPath = '/TopStordata'
      isExist = os.path.exists(dirPath)
      if not isExist:
        os.makedirs(dirPath)
      filename =  uploaded_file.filename.replace(' ', '')
      filePath = os.path.join(dirPath, filename)
      uploaded_file.save(filePath)
      cmdline = 'python /TopStor/UsersMassAddition.py '+ leaderip +' '+ data['user'] + ' ' + myhost + ' ' + filePath + ' '+ data['token']
      postchange(cmdline)
      logmsg.sendlog('Unlin1025', 'info', data["user"])
      return {"data":data}
    else:
      logmsg.sendlog('Unlin1026', 'error', data["user"])
      return 'Error while uploading file!'


def postchange(cmndstring,host='leader'):
 global leaderip, myhost, myhostip
 z= cmndstring.split(' ')
 if 'leader' in host:
    host=myhost
 msg={'req': 'Pumpthis', 'reply':z}
 if myhost in host:
     ownerip=myhostip
 else:
    try:
        ownerip=get('ready/'+host,'--prefix')[0][1]
    except:
        return 'host is not ready'
 with open('/TopStordata/tempdata','a') as f:
  f.write(str((ownerip, str(msg),'recvreply',myhost))+'\n')
 sendhost(ownerip, str(msg),'recvreply',myhost)
 return 'queued'

def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

def getusers():
 global leaderip
 userlst = etcdgetjson(leaderip,'usersinfo','--prefix') 
 uid = 0
 users = []
 for user in userlst:
  username = user['name'].replace('usersinfo/','')
  users.append([username,str(uid)]) 
  uid += 1
 return users

def getgroups():
 global leaderip
 groupslst = etcdgetjson(leaderip, 'usersigroup','--prefix') 
 gid = 0
 groups = []
 for group in groupslst:
  grpusers = group['prop'].split('/')[2]
  groupname = group['name'].replace('usersigroup/','')
  groups.append([groupname,str(gid), grpusers]) 
  gid += 1
 return groups

@app.route('/', methods=['GET'])
def home():
    return '''<h1>Distant Reading Archive</h1>
<p>A prototype API for distant reading of science fiction novels.</p>'''

def gettenants():
 global leaderip
 vols = get('vol','/NFS/')
 volinfo = []
 pid = 0
 for vol in vols:
  if 'active' not in vol[1]:
    continue
  volinfo.append({'id':pid, 'pool': vol[0].split('/')[3], 'text':vol[0].split('/')[4]})
  pid += 1
 return volinfo

def getpools():
 global pooldict, leaderip
 pools = get('pools/','--prefix')
 poolinfo = []
 pid = 0
 for pool in pools:
  poolinfo.append({'id':pid, 'owner': pool[1], 'text':pool[0].split('/')[1]})
  pid += 1
  pooldict[pool[0].split('/')[1]] = {'id': pid, 'owner': pool[1] }
 return poolinfo

@app.route('/api/v1/volumes/connections', methods=['GET','POST'])
@login_required
@audit_command
def getconns(data):
 global leaderip
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 conns = get('connections/user','--prefix')
 devs = get('connections/dev','--prefix')
 zippedconns = zip(conns,devs)
 conndict =  {}
 conndict['connections'] = []
 tconns = 0
 for conn in zippedconns:
  volume = conn[0][0].split('/')[3]
  subconns =  conn[0][1].split('/')
  subdevs =  conn[1][1].split('/')
  zippedsubconns = zip(subconns,subdevs)
  for subcon in zippedsubconns:
   conndict['connections'].append({"volume": volume, 'user': subcon[0], 'device':subcon[1] })
 conndict['response'] = data['response']
 return conndict 


@app.route('/api/v1/software/setversion', methods=['GET','POST'])
@login_required
@audit_command
def setversion(data):
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 versions = getversions()
 if data['version'] in versions['versions'] and data['version'] not in versions['current']:
  cmdline = '/TopStor/updateversion '+data['version']
  postchange(cmdline)
 return data 

@app.route('/api/v1/software/versions', methods=['GET','POST'])
@login_required
@audit_command
def versions(data):
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 res = getversions()
 res['response'] = data['response']
 return res

@app.route('/api/v1/software/apply', methods=['GET','POST'])
@login_required
@audit_command
def swapply(data):
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 cmdline = '/TopStor/systemcheckout.sh '+data['version'].split(',')[0]+' '+data['response']+' '+'hi1'
 postchange(cmdline)
 versionlst = versions(data)
 versionlst['response'] = data['response']
 return versionlst


#@app.route('/api/v1/hosts/info', methods=['GET','POST'])
def hostsinfo():
 global allhosts, readyhosts, activehosts, losthosts, possiblehosts
 allhosts = Hostsconfig.getall()
 return jsonify(allhosts)

@app.route('/api/v1/hosts/allinfo', methods=['GET','POST'])
@login_required
@audit_command
def hostsallinfo(data):
 global allhosts, readyhosts, activehosts, losthosts, possiblehosts
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 hostsinfo()
 hostslost()  
 hostspossible()
 return jsonify({'all': allhosts, 'active': activehosts, 'ready':readyhosts, 'possible':possiblehosts, 'lost':losthosts})


#@app.route('/api/v1/hosts/ready', methods=['GET','POST'])
def hostsready():
 global allhosts, readyhosts, activehosts, losthosts, possiblehosts, leaderip
 hosts = get('ready','--prefix')
 readyhosts = []
 hid = 0
 for host in hosts:
  name = host[0].replace('ready/','')
  ip = host[1]
  readyhosts.append({'name':name, 'ip': ip, 'id': hid}) 
  hid +=1
 return jsonify(readyhosts)

#@app.route('/api/v1/hosts/active', methods=['GET','POST'])
def hostsactive():
 global allhosts, readyhosts, activehosts, losthosts, possiblehosts, leaderip
 hosts = get('ActivePartners','--prefix')
 activehosts = []
 hid = 0
 for host in hosts:
  name = host[0].replace('ActivePartners/','')
  ip = host[1]
  activehosts.append({'name':name, 'ip': ip, 'id': hid}) 
  hid +=1
 return jsonify(activehosts)


@app.route('/api/v1/hosts/syncnow', methods=['GET','POST'])
@login_required
@audit_command
def hostssyncnow(data):
 # "Nodes Not in Sync" -> Sync now: every ready node applies the pending sync requests at once (the step its
 # syncrequestlooper repeats on its own), so the cluster does not wait for the next round of the loop
 global leaderip
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 asked = []
 for host in get('ready','--prefix'):
  name = host[0].replace('ready/','')
  postchange('/pace/checksyncs.py syncrequest '+leaderip+' '+name+' '+host[1], name)
  asked.append(name)
 data['syncnow'] = asked
 return data

@app.route('/api/v1/hosts/discover', methods=['GET','POST'])
@login_required
@audit_command
def discover(data):
    if 'baduser' in data['response']:
      return {'response': 'baduser'}
    cmndstring = '/TopStor/getdiscovery.sh '
    postchange(cmndstring)
    return data 

#@app.route('/api/v1/hosts/possible', methods=['GET','POST'])
def hostspossible():
 global allhosts, readyhosts, activehosts, losthosts, possiblehosts, leaderip
 hosts = get('possible','--prefix')
 possiblehosts = []
 hid = 0
 for host in hosts:
  name = host[0].split('/')[1]
  ip = host[1]
  possiblehosts.append({'name':name, 'ip': ip, 'id': hid}) 
  hid +=1
 return jsonify(possiblehosts)

#@app.route('/api/v1/hosts/lost', methods=['GET','POST'])
def hostslost():
 global allhosts, readyhosts, activehosts, losthosts, possiblehosts
 hostsready()
 hostsactive()
 losthosts = []
 hid = 0
 for active in activehosts:
  if active['name'] not in str(readyhosts): 
   losthosts.append({'id': hid, 'name': active['name'], 'ip': active['ip']})
  hid += 1
 return jsonify(losthosts)

@app.route('/api/v1/pools/dgsinfo', methods=['GET','POST'])
@login_required
@audit_command
def dgsinfo(data):
 global allinfo 
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 getalltime()
 dgsinfo = {'raids':allinfo['raids'], 'pools':allinfo['pools'], 'disks':allinfo['disks']}
 dgsinfo['newraid'] = newraids(allinfo['disks'])
 return jsonify(dgsinfo)

@app.route('/api/v1/pools/delpool', methods=['GET','POST'])
@login_required
@audit_command
def dgsdelpool(data):
 global allinfo, myhost
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 getalltime()
 # a pool that still holds volumes (NFS/CIFS/HOME/iSCSI) is not destroyed: their share containers and targets use its
 # datasets, so the forced export/destroy would hang (container flavour) or cut running clients
 if any(vinfo.get('pool') == data['pool'] for vinfo in allinfo['volumes'].values()):
  logmsg.sendlog('DGfa25','error','system',data['pool'])
  data['error'] = 'poolhasvolumes'
  return jsonify(data)
 data['owner'] = allinfo['pools'][data['pool']]['host']
 ownerip = allinfo['hosts'][data['owner']]['ipaddress']
 datastr = data['pool']+' '+data['user'] 
 cmndstring = '/TopStor/DGdestroyPool '+leaderip+' '+datastr
 postchange(cmndstring,data['owner'])
# z= cmndstring.split(' ')
# msg={'req': 'Pumpthis', 'reply':z}
# sendhost(ownerip, str(msg),'recvreply',myhost)
 return jsonify(data)

@app.route('/api/v1/pools/addtopool', methods=['GET','POST'])
@login_required
@audit_command
def dgsaddtopool(data):
 global allinfo, myhost, leaderip
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 getalltime('yes')
 keys = []
 dgsinfo = {'raids':allinfo['raids'], 'pools':allinfo['pools'], 'disks':allinfo['disks']}
 dgsinfo['newraid'] = newraids(allinfo['disks'])
 if data['useable'] not in dgsinfo['newraid'][data['redundancy']]:
  keys = list(dgsinfo['newraid'][data['redundancy']].keys())
  keys.append(float(data['useable']))
  keys.sort()
  diskindx = keys.index(float(data['useable'])) + 1
  if diskindx == len(keys):
   diskindx = len(keys) - 2 
  data['useable'] = keys[diskindx]
 disks =  dgsinfo['newraid'][data['redundancy']][data['useable']]
 if 'single' in data['redundancy']:
  selecteddisks = disks
 elif 'raid10' in data['redundancy']:
  bestdisks = selectraid10(leaderip,disks,allinfo['disks'],data['pool'])
  if len(bestdisks) < 1:
    return jsonify(data)
  selecteddisks = bestdisks.split(',')
 elif 'raid50' in data['redundancy']:
  bestdisks = selectraid50(leaderip,disks,allinfo['disks'],data['pool'])
  if len(bestdisks) < 1:
    return jsonify(data)
  selecteddisks = bestdisks.split(',')
 elif 'raid60' in data['redundancy']:
  bestdisks = selectraid60(leaderip,disks,allinfo['disks'],data['pool'])
  if len(bestdisks) < 1:
    return jsonify(data)
  selecteddisks = bestdisks.split(',')
 else:
  print('#########################')
  print('disks',disks)
  print('#########################')
  bestdisks = selectdisks(leaderip,disks,allinfo['disks'],data['pool'])
  print('bestdisks',bestdisks)
  print('#########################')
  if len(bestdisks) < 1:
    return jsonify(data)
  selecteddisks = bestdisks.split(',')
 data['owner'] = allinfo['pools'][data['pool']]['host']
 ownerip = allinfo['hosts'][data['owner']]['ipaddress']
 diskstring = ''
 for dsk in selecteddisks:
  diskstring += dsk+":"+dsk[-5:]+" "
 if 'mirror' in data['redundancy']:
  datastr = 'addmirror '+data['user']+' '+data['owner']+" "+diskstring+data['pool']
 elif 'volset' in data['redundancy']:
  datastr = 'addstripeset '+data['user']+' '+data['owner']+" "+diskstring+data['pool']
 elif 'raid5' in data['redundancy'] and 'raid50' not in data['redundancy']:     # 'raid5' is a substring of 'raid50': a RAID50 request used to be created as one big single-parity group (QSD5.269)
  datastr = 'addparity '+data['user']+' '+data['owner']+" "+diskstring+data['pool']
 elif 'raid6plus' in data['redundancy']:
  datastr = 'addparity3 '+data['user']+' '+data['owner']+" "+diskstring+data['pool']
 elif 'raid10' in data['redundancy']:
  datastr = 'addraid10 '+data['user']+' '+data['owner']+" "+diskstring+data['pool']
 elif 'raid50' in data['redundancy']:
  datastr = 'addraid50w'+str(raidwidthof('raid50',selecteddisks,allinfo['disks']))+' '+data['user']+' '+data['owner']+" "+diskstring+data['pool']
 elif 'raid60' in data['redundancy']:
  datastr = 'addraid60w'+str(raidwidthof('raid60',selecteddisks,allinfo['disks']))+' '+data['user']+' '+data['owner']+" "+diskstring+data['pool']
 elif 'raid6' in data['redundancy']:
  datastr = 'addparity2 '+data['user']+' '+data['owner']+" "+diskstring+data['pool']
 cmndstring = '/TopStor/DGsetPool '+leaderip+' '+datastr
 postchange(cmndstring,data['owner'])
# z= cmndstring.split(' ')
# msg={'req': 'Pumpthis', 'reply':z}
# sendhost(ownerip, str(msg),'recvreply',myhost)
 return jsonify(data)
 
@app.route('/api/v1/pools/cachespares', methods=['GET', 'POST'])
@login_required
@audit_command
def dgscachespares(data):
    global allinfo, myhost, leaderip

    if 'baduser' in data['response']:
        return {'response': 'baduser'}

    try:
        cache_disks = data.get('cache_disks', [])
        if not cache_disks:
            return jsonify({'response': 'success', 'message': 'No disks to add.'})

        disk_ids_to_store = []
        for disk_id in cache_disks:
            if disk_id.startswith('/dev/disk/by-id/'):
                disk_ids_to_store.append(disk_id.split('/')[-1])
            else:
                disk_ids_to_store.append(disk_id)

        disks_str = " ".join(disk_ids_to_store)
        cmndstring = f"/TopStor/cachedisks.py add {leaderip} {disks_str}"
        postchange(cmndstring)

        return jsonify({
            'response': 'success',
            'message': f'Queued adding of {len(disk_ids_to_store)} spare cache disks.'
        })

    except Exception as e:
        return jsonify({
            'response': 'error',
            'message': f'Failed to process spare cache list via etcd: {str(e)}'
        }), 500

@app.route('/api/v1/pools/delcachespares', methods=['GET', 'POST'])
@login_required
@audit_command
def dgsdelcachespares(data):
    global allinfo, myhost, leaderip

    if 'baduser' in data['response']:
        return {'response': 'baduser'}

    try:
        disks_to_remove = data.get('cache_disks', [])

        if not disks_to_remove:
            return jsonify({'response': 'error', 'message': 'No disks provided'}), 400

        disks_str = " ".join(disks_to_remove)

        cmndstring = f"/TopStor/cachedisks.py del {leaderip} {disks_str}"
        postchange(cmndstring)

        return jsonify({
            'response': 'success',
            'message': f'Queued removal of {len(disks_to_remove)} cache disks'
        })

    except Exception as e:
        print(f"Error processing request: {e}")
        return jsonify({
            'response': 'error',
            'message': f'Failed to queue cache disk removal: {str(e)}'
        }), 500

@app.route('/api/v1/pools/newpool', methods=['GET','POST'])
@login_required
@audit_command
def dgsnewpool(data):
    global allinfo, myhost, leaderip

    if 'baduser' in data['response']:
        return {'response': 'baduser'}

    getalltime('yes')

    manual_cache = data.get('cache')
    if not isinstance(manual_cache, list):
        manual_cache = []
    excluded_disks = ','.join(manual_cache)

    if data.get('disks'):
        selecteddisks = data['disks']
    else:
        keys = []
        dgsinfo = {'raids':allinfo['raids'], 'pools':allinfo['pools'], 'disks':allinfo['disks']}
        dgsinfo['newraid'] = newraids(allinfo['disks'])

        if data['useable'] not in dgsinfo['newraid'][data['redundancy']]:
            keys = list(dgsinfo['newraid'][data['redundancy']].keys())
            keys.append(float(data['useable']))
            keys.sort()
            diskindx = keys.index(float(data['useable'])) + 1
            if diskindx == len(keys):
                diskindx = len(keys) - 2
            data['useable'] = keys[diskindx]

        disks = dgsinfo['newraid'][data['redundancy']][data['useable']]

        if 'single' in data['redundancy']:
            selecteddisks = [disk for disk in disks if disk not in manual_cache][:1]
        elif 'raid10' in data['redundancy']:
            bestdisks = selectraid10(leaderip, disks, allinfo['disks'], '', excluded_disks)
            if len(bestdisks) < 1:
                return jsonify({
                    'response': 'error',
                    'message': 'No eligible data disks are available for this configuration'
                }), 400
            selecteddisks = bestdisks.split(',')
        elif 'raid50' in data['redundancy']:
            bestdisks = selectraid50(leaderip, disks, allinfo['disks'], '', excluded_disks)
            if len(bestdisks) < 1:
                return jsonify({
                    'response': 'error',
                    'message': 'No eligible data disks are available for this configuration'
                }), 400
            selecteddisks = bestdisks.split(',')
        elif 'raid60' in data['redundancy']:
            bestdisks = selectraid60(leaderip, disks, allinfo['disks'], '', excluded_disks)
            if len(bestdisks) < 1:
                return jsonify({
                    'response': 'error',
                    'message': 'No eligible data disks are available for this configuration'
                }), 400
            selecteddisks = bestdisks.split(',')
        else:
            bestdisks = selectdisks(leaderip, disks, allinfo['disks'], '', excluded_disks)
            if len(bestdisks) < 1:
                return jsonify({
                    'response': 'error',
                    'message': 'No eligible data disks are available for this configuration'
                }), 400
            selecteddisks = bestdisks.split(',')

    if not selecteddisks:
        return jsonify({
            'response': 'error',
            'message': 'No eligible data disks are available for this configuration'
        }), 400

    diskstring = ''
    for dsk in selecteddisks:
        diskstring += dsk+":"+dsk[-5:]+" "

    data['owner'] = allinfo['disks'][selecteddisks[0]]['host']
    ownerip = allinfo['hosts'][data['owner']]['ipaddress']

    cachestring = ''
    if str(data.get('cache_bool')).lower() == 'true':

        if manual_cache and isinstance(manual_cache, list) and len(manual_cache) > 0:
            cachestring = "cache "
            for dsk in manual_cache:
                cachestring += dsk+":"+dsk[-5:]+" "

        else:
            cache_candidates = []
            if 'raids' in allinfo:
                for raid_data in allinfo['raids'].values():
                    if raid_data.get('status') == 'cache':
                         for d in raid_data.get('disks', []):
                             disk_info = allinfo['disks'].get(d)
                             if disk_info and disk_info.get('host') == data['owner']:
                                 cache_candidates.append(d)

            available_cache = [d for d in cache_candidates if d not in selecteddisks]
            if available_cache:
                cid = available_cache[0]
                cachestring = "cache " + cid + ":" + cid[-5:] + " "
            else:
                print(f"LOG: Cache requested for host {data['owner']}, but no matching disks found in Spare Cache List.")

    print('#############################3')
    print(';;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;')
    print(selecteddisks)
    print('#########################333')

    data['owner'] = allinfo['disks'][selecteddisks[0]]['host']
    ownerip = allinfo['hosts'][data['owner']]['ipaddress']

    if 'single' in data['redundancy']:
        datastr = 'Single '+data['user']+' '+data['owner']+" "+selecteddisks[0]+" "+selecteddisks[0][-5:]+" "+cachestring+" "+" nopool "+data['user']+" "+data['owner']
    elif 'mirror' in data['redundancy']:
        datastr = 'mirror '+data['user']+' '+data['owner']+" "+diskstring+" "+cachestring+" "+"nopool "+data['user']+" "+data['owner']
    elif 'volset' in data['redundancy']:
        datastr = 'stripeset '+data['user']+' '+data['owner']+" "+diskstring+" "+cachestring+" nopool "+data['user']+" "+data['owner']
    elif 'raid5' in data['redundancy'] and 'raid50' not in data['redundancy']:     # see addtopool: raid50 must not be taken for raid5
        datastr = 'parity '+data['user']+' '+data['owner']+" "+diskstring+" "+cachestring+" nopool "+data['user']+" "+data['owner']
    elif 'raid6plus' in data['redundancy']:
        datastr = 'parity3 '+data['user']+' '+data['owner']+" "+diskstring+" "+cachestring+" nopool "+data['user']+" "+data['owner']
    elif 'raid10' in data['redundancy']:
        datastr = 'raid10 '+data['user']+' '+data['owner']+" "+diskstring+" "+cachestring+" nopool "+data['user']+" "+data['owner']
    elif 'raid50' in data['redundancy']:
        datastr = 'raid50w'+str(raidwidthof('raid50',selecteddisks,allinfo['disks']))+' '+data['user']+' '+data['owner']+" "+diskstring+" "+cachestring+" nopool "+data['user']+" "+data['owner']
    elif 'raid60' in data['redundancy']:
        datastr = 'raid60w'+str(raidwidthof('raid60',selecteddisks,allinfo['disks']))+' '+data['user']+' '+data['owner']+" "+diskstring+" "+cachestring+" nopool "+data['user']+" "+data['owner']
    elif 'raid6' in data['redundancy']:
        datastr = 'parity2 '+data['user']+' '+data['owner']+" "+diskstring+" "+cachestring+" nopool "+data['user']+" "+data['owner']

    cmndstring = '/TopStor/DGsetPool '+leaderip+' '+datastr+' '+data['user']
    postchange(cmndstring, data['owner'])

    return jsonify(data)

@app.route('/api/v1/pools/updatecache', methods=['GET','POST'])
@login_required
@audit_command
def dgsupdatecache(data):
    global allinfo, myhost, leaderip

    if 'baduser' in data['response']:
        return {'response': 'baduser'}

    getalltime('yes')

    pool = data.get('pool')
    if not pool or pool not in allinfo['pools']:
        return jsonify({'response': 'error', 'message': 'Pool not found'}), 400

    pool_data = allinfo['pools'][pool]
    old_cache_disks = []
    for raidId in pool_data.get('raids', []):
        if raidId.split('_')[0] == 'cache':
            old_cache_disks.extend(allinfo['raids'].get(raidId, {}).get('disks', []))

    data['owner'] = pool_data['host']

    new_cache = data.get('cache')
    if not isinstance(new_cache, list):
        new_cache = [new_cache] if new_cache else []

    if not new_cache:
        # Smart auto-selection: rather than a fixed size cutoff, look at
        # every free disk on this pool's owner node and treat whichever
        # ones sit at the smallest size tier - strictly smaller than the
        # rest - as the natural cache candidates (mirrors how a real
        # deployment usually mixes a few small fast disks in with a
        # majority of larger ones). If every disk on the host is the same
        # size, nothing stands out as "smaller than the rest", so there's
        # no candidate.
        free_disk_ids = allinfo['raids'].get('free', {}).get('disks', [])
        local_sizes = {}
        for did in free_disk_ids:
            disk_info = allinfo['disks'].get(did, {})
            if disk_info.get('host') != data['owner']:
                continue
            if did in old_cache_disks:
                continue
            try:
                local_sizes[did] = float(disk_info.get('size', 0))
            except (TypeError, ValueError):
                continue

        if local_sizes:
            smallest = min(local_sizes.values())
            largest = max(local_sizes.values())
            if smallest < largest:
                candidates = [d for d, s in local_sizes.items() if s == smallest]
                new_cache = [min(candidates, key=lambda d: local_sizes[d])]

    if not new_cache:
        return jsonify({
            'response': 'error',
            'message': f'No disk on {data["owner"]} is smaller than the rest to use as cache'
        }), 400

    new_cache = [d for d in new_cache if d not in old_cache_disks]
    if not new_cache:
        return jsonify({
            'response': 'error',
            'message': 'Selected disk is already this pool\'s cache'
        }), 400

    ownerip = allinfo['hosts'][data['owner']]['ipaddress']

    oldstring = ''
    for dsk in old_cache_disks:
        oldstring += dsk+":"+dsk[-5:]+" "
    newstring = ''
    for dsk in new_cache:
        newstring += dsk+":"+dsk[-5:]+" "

    datastr = 'updatecache '+data['user']+' '+data['owner']+' '+pool+' old '+oldstring+'new '+newstring+'nopool'
    cmndstring = '/TopStor/DGsetPool '+leaderip+' '+datastr+' '+data['user']
    postchange(cmndstring, data['owner'])

    return jsonify(data)

@app.route('/api/v1/volumes/stats', methods=['GET','POST'])
@login_required
@audit_command
def volumestats(data):
 global allinfo 
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 getalltime()
 volstats = allvolstats(leaderip, deepcopy(allinfo))
 return jsonify(volstats)

@app.route('/api/v1/volumes/volumelist', methods=['GET','POST'])
@login_required
@audit_command
def volumeslist(data):
 global allinfo 
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 getalltime()
 volumes = []
 vid = 0
 for vol in allinfo['volumes']:
  volumes.append({'id':vid, 'text':vol.split('_')[0], 'fullname':vol,'pool':allinfo['volumes'][vol]['pool']})
  vid += 1
 return jsonify(volumes)

@app.route('/api/v1/tenants/tenantinfo', methods=['GET','POST'])
@login_required
@audit_command
def tenantsinfo(data):
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 alltenants = gettenants()
 alltenants.append({'id':len(allpools), 'text':'Cluster'})
 return jsonify({'results':alltenants})



@app.route('/api/v1/volumes/poolsinfo', methods=['GET','POST'])
@login_required
@audit_command
def volpoolsinfo(data):
 global allpools
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 allpools = getpools()
 return jsonify({'results':allpools})


@app.route('/api/v1/volumes/snapshots/snapshotsinfo', methods=['GET','POST'])
@login_required
@audit_command
def volumessnapshotsinfo(data):
 global allvolumes, alldsks, allinfo
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 snaplist = {'Once':[], 'Minutely': [], 'Hourly': [], 'Weekly':[], '-':[]}
 periodlist = {'Minutely': [], 'Hourly': [], 'Weekly':[], 'Trend': []}
 getalltime()
 allgroups = getgroups()
 alllist = []
 snappriods = []
 for snap in allinfo['snapshots']:
  allinfo['snapshots'][snap]['date'] = datetime.strptime(allinfo['snapshots'][snap]['creation'], '%a %b %d %Y').strftime('%d-%B-%Y')
  print('#####################################33')
  print(allinfo['snapshots'][snap])
  print('#####################################33')
  snaplist[allinfo['snapshots'][snap]['snaptype']].append(allinfo['snapshots'][snap].copy())
  alllist.append(allinfo['snapshots'][snap].copy())
 for period in allinfo['snapperiods']:
  snappriods.append(allinfo['snapperiods'][period].copy())
  periodlist[allinfo['snapperiods'][period]['periodtype']].append(allinfo['snapperiods'][period].copy())
 return jsonify({'allsnaps':alllist, 'Once':snaplist['Once'], 'Hourly':snaplist['Hourly'], 'Weekly':snaplist['Weekly'], 'Minutely':snaplist['Minutely'] ,'allperiods':snappriods, 'Minutelyperiod':periodlist['Minutely'], 'Hourlyperiod':periodlist['Hourly'], 'Weeklyperiod':periodlist['Weekly']})

def volumesinfo(prot='all'):
 global allvolumes, alldsks, allinfo
 getalltime()
 allgroups = getgroups()
 volgrouplist = []
 volumes = []
 if prot == 'all':
   prot = 'S'
   prot2 = 'H'
 else:
  prot2 = prot
 for volume in allinfo['volumes']:
  if prot in allinfo['volumes'][volume]['prot'] or prot2 in allinfo['volumes'][volume]['prot']:
   volgrps = []
   if prot in ['CIFS','NFS']:
    print('##################')
    print(allinfo['volumes'][volume])
    print('##################')
    try:
        volgrouplist =  deepcopy(allinfo['volumes'][volume]['groups'])
    except:
        volgrouplist=[]
    if type(volgrouplist) != list:
      volgrouplist = volgrouplist.split(',')
    for group in allgroups:
     if group[0] in volgrouplist:
      volgrps.append(group[1])
   volumes.append(deepcopy(allinfo['volumes'][volume]))
   volumes[-1]['groups'] = deepcopy(volgrps)
 return volumes


@app.route('/api/v1/volumes/CIFS/volumesinfo', methods=['GET','POST'])
@login_required
@audit_command
def volumescifsinfo(data):
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 volumes = volumesinfo('CIFS') 
 return jsonify({'allvolumes':volumes})

@app.route('/api/v1/volumes/ISCSI/volumesinfo', methods=['GET','POST'])
@login_required
@audit_command
def volumesiscsiinfo(data):
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 volumes = volumesinfo('ISCSI') 
 return jsonify({'allvolumes':volumes})

@app.route('/api/v1/volumes/NFS/volumesinfo', methods=['GET','POST'])
@login_required
@audit_command
def volumesnfsinfo(data):
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 volumes = volumesinfo('NFS') 
 return jsonify({'allvolumes':volumes})

@app.route('/api/v1/volumes/HOME/volumesinfo', methods=['GET','POST'])
@login_required
@audit_command
def volumeshomeinfo(data):
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 volumes = volumesinfo('HOME') 
 return jsonify({'allvolumes':volumes})

@app.route('/api/v1/volumes/volumesinfo', methods=['GET','POST'])
@login_required
@audit_command
def volumesallinfo(data):
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 volumes = volumesinfo() 
 return jsonify({'allvolumes':volumes})



@app.route('/api/v1/pools/poolsinfo', methods=['GET','POST'])
@login_required
@audit_command
def poolsinfo(data):
 global allpools
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 allpools = getpools()
 allpools.append({'id':len(allpools), 'text':'-------'})
 return jsonify({'results':allpools})

@app.route('/api/v1/groups/groupchange', methods=['GET','POST'])
@login_required
@audit_command
def pgroupchange(data):
 global leaderip
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 if data.get('name') == EVERYONE:
  data['error'] = 'everyonefixed'     # all users are always members of Everyone: nobody changes it
  return data
 usrs = data.get('users')
 usrstr = ''
 if len(usrs) < 1:
  usrstr = 'NoUser'
 else:
  for usr in usrs.split(','):
   for suser in allusers:
    if str(suser['id']) == str(usr):
     usrstr += suser['name']+',' 
  usrstr = usrstr[:-1]
 cmndstring = '/TopStor/UnixChangeGroup '+leaderip+' '+' '+data.get('name')+' users'+usrstr+' '+data['user']+' '+'change'
 postchange(cmndstring)
 return data

@app.route('/api/v1/replication/addpartner', methods=['GET','POST'])
@login_required
@audit_command
def partneradd(data):
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 cmndstring = '/TopStor/PartnerAdd.py '+data.get('partnerip')+' '+data.get('partneralias')+' '+data.get('replitype')+' '+data.get('repliport')+' '+data.get('phrase')+' '+data.get('user')
 postchange(cmndstring)
 return data

@app.route('/api/v1/users/userchange', methods=['GET','POST'])
@login_required
@audit_command
def userchange(data):
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 grps = data.get('groups')
 groupstr = ''
 if 'Cluster' not in data['tenant']:
     volinfo = get('vol',data['tenant'])[0]
     data['owner'] = 'leader'
     if 'NFS' in volinfo[0]:
        data['owner'] = volinfo[0].split('/')[2]
        pool = volinfo[0].split('/')[3]
        if len(grps) < 3:
            grps = 'NoGroup'
     cmndstring = '/TopStor/TenantChangeUser '+leaderip+' '+data['name']+' groups'+grps+' '+pool+' '+data['tenant']+' '+data['response']
     postchange(cmndstring,data['owner'])
     return data
 allgroups = getgroups()
 if len(grps) < 1:
  groupstr = 'NoGroup'
 else:
  for grp in grps.split(','):
   groupstr += allgroups[int(grp)][0]+','
  groupstr = groupstr[:-1]
 groupstr = with_everyone(groupstr)
 cmndstring = '/TopStor/UnixChangeUser '+leaderip+' '+data.get('name')+' groups'+groupstr+' '+data['user']+' '+'change'
 postchange(cmndstring)
 return data


 
@app.route('/api/v1/info/onedaylog', methods=['GET','POST'])
@login_required
@audit_command
def getonedaylog(data):
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 result = onedaylog() 
 return result 
@app.route('/api/v1/info/logs', methods=['GET','POST'])
@login_required
@audit_command
def getalllogs(data):
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 notif = getlogs()
 return jsonify({'alllogs': notif})

@app.route('/api/v1/info/commandlog', methods=['GET','POST'])
@login_required
@audit_command
def getcommandlog(data):
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 commands = get_command_log(data['user'])
 return jsonify({'response': 'Ok', 'commands': commands})


@app.route('/api/v1/login/renewtoken', methods=['GET','POST'])
@login_required
@audit_command
def renewtoken(data):
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 user = loggedusers[data['token']]['user']
 setlogin(leaderip,myhost, user,'!',data['token'])
 return data


@app.route('/api/v1/info/cversion', methods=['GET','POST'])
@login_required
@audit_command
def getcversion(data):
    global leaderip, leader, myhost
    if 'baduser' in data['response']:
      return {'response': 'baduser'}
    #cmdline='/TopStor/getcversion.sh '+leaderip+' '+leader+' '+myhost
    #subprocess.run(cmdline,stdout=subprocess.PIPE)
    #postchange(cmdline)
    cversions = get('cversion/'+myhost)[0]
    return { 'response':'Ok', 'cversion': str(cversions)}



@app.route('/api/v1/info/notification', methods=['GET','POST'])
@login_required
@audit_command
def getnotification(data):
 global leaderip, myhost
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 notifbody = get('notification')[0].split(' ')[1:]
 isinsync = get('isinsync')[0]
 with open('/root/tmptmp','a') as f:
    f.write(str(notifbody))
 requests = get('request','--prefix')
 requestdict = {}
 for req in requests:
  reqname = req[0].split('/')[1]
  reqhost = req[0].split('/')[2]
  reqstatus = req[1]
  if reqname not in requestdict:
   requestdict[reqname] = {}
  requestdict[reqname][reqhost] = reqstatus
 try:
    msg = logdict[notifbody[3]]
 except:
    msg = logdict['NotSupported']
    logmsg.sendlog('NotSupported','warning','system',str(notifbody))
    return { 'isinsync': isinsync, 'response': 'Ok' }
    
   
 msgbody = '.'
 notifc = 6
 for word in msg[4:]:
  if word == ':':
   try:
    msgbody = msgbody[:-1]+' '+notifbody[notifc]+'.'
   except:
    msgbody = str(msgbody) +' '+str(notifbody)+'parseerror.'
    with open('/root/fapierror','w') as f:
     f.write(msgbody+'\n'+str(msg))
    print('notification parse error')
    print('############################')
   notifc += 1
  elif len(word) > 0:
   msgbody = msgbody[:-1]+' '+word+'.' 
 notif = { 'isinsync': isinsync, 'importance':msg[0].replace(':',''), 'msgcode': notifbody[3], 'date':notifbody[0], 'time':notifbody[1],
	 'host':notifbody[2], 'type':notifbody[4], 'user': notifbody[5], 'msgbody': msgbody[1:],'requests':requestdict, 'response':'Ok'}
 return jsonify(notif)

@app.route('/api/v1/volumes/snapshots/create', methods=['GET','POST'])
@login_required
@audit_command
def volumesnapshotscreate(data):
 global allinfo, myhost
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 datastr = ''
 getalltime()
 ownerip = allinfo['hosts'][data['owner']]['ipaddress']
 data['leaderip'] = leaderip
 switch = { 'Once':['snapsel','leaderip', 'name','pool','volume'], 'Minutely':['snapsel', 'leaderip', 'pool', 'volume', 'every', 'keep'],
	'Hourly':['snapsel', 'leaderip', 'pool', 'volume', 'sminute', 'every', 'keep'], 
	'Weekly':['snapsel', 'leaderip', 'pool', 'volume', 'stime', 'every', 'keep'] }
 datastr = ''
 for param in switch[data['snapsel']]:
  datastr +=data[param]+' '
 #datastr = data['name']+' '+data['pool']+' '+data['volume']+' '+data['user']
 if 'receiver' not in data:
   datastr += 'NoReceiver'
 else:
   datastr += data['receiver']
 print('#############################')
 print(data)
 print(datastr)
 print('###########################')
 cmndstring = '/TopStor/SnapshotCreate'+datastr+' '+data['user']
 postchange(cmndstring,data['owner'])
# z= cmndstring.split(' ')
# msg={'req': 'Pumpthis', 'reply':z}
# sendhost(ownerip, str(msg),'recvreply',myhost)
 return data





@app.route('/api/v1/volumes/create', methods=['GET','POST'])
@login_required
@audit_command
def volumecreate(data):
 global allinfo, myhost
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 datastr = ''
 datatype = ip_context(data['type'], data.get('domname',''))
 isvu =  int(is_valid_ip(data['ipaddress']))+int(is_unique_ip(data['ipaddress'],datatype))+int(is_unique_name(data['name']))
 if isvu == 0:
    print('ip is valid')
 else:
    if isvu < 100:
        logmsg.sendlog('IPaddrfa','error','system',loggedusers[data['token']]['user'])
    elif isvu > 10 and isvu < 120: 
        logmsg.sendlog('IPadduqfa','error','system',loggedusers[data['token']]['user'])
    else:
        logmsg.sendlog('IPnamuqfa','error','system',loggedusers[data['token']]['user'])
    return data
 getalltime()
 if data.get('pool') not in allinfo['pools']:
  # no pool chosen / unknown pool: a clean answer instead of a KeyError (HTTP 500)
  logmsg.sendlog('Volpool1','error',loggedusers[data['token']]['user'],str(data.get('name','')))
  data['error'] = 'nopool'
  return data
 ownerip = allinfo['hosts'][allinfo['pools'][data['pool']]['host']]['ipaddress']
 data['owner'] = allinfo['hosts'][allinfo['pools'][data['pool']]['host']]['name']
 if 'groups' in data:
  data['groups'] = only_everyone(data['groups'])
 if 'ISCSI' in data['type']:
  data['chapuser']='MoatazNegm'
  data['chappas']='MezoAdmin'
  datastr = data['pool']+' '+data['name']+' '+data['size']+' '+data['ipaddress']+' '+data['Subnet']+' '+data['portalport']+' '+data['initiators']+' '+data['chapuser']+' '+data['chappas']+' '+data['active']+' '+data['user']+' '+data['owner']+' '+data['user']
 elif 'CIFSdom' in data['type']:

  cmdline=['/TopStor/encthis.sh',data["domname"],data["dompass"]]
  data["dompass"]=subprocess.run(cmdline,stdout=subprocess.PIPE).stdout.decode().split('_result')[1].replace('/','@@sep')

  # Call resolve script if domsrv is passed and domip is not
  if data['domsrv']:
    cmdline = ['/TopStor/resolve_dns.sh', leaderip, data['domsrv']]
    result = subprocess.run(cmdline, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output = result.stdout.decode().strip()

    resolved_ip = ""
    if output.startswith("RESOLVEDIP="):
     resolved_ip = output.split("=")[1]

    if resolved_ip and int(is_valid_ip(resolved_ip)) == 0:
     data['domip'] = resolved_ip
     data['domsrv'] = ''
    else:
     logmsg.sendlog('CIFS1033', 'error', 'system', data['name'],data["domsrv"])
     return data

  datastr = data['pool']+' '+data['name']+' '+data['size']+' '+' '+data['ipaddress']+' '+data['Subnet']+' '+data['active']+' '+data['user']+' '+data['owner']+' '+data['user']+' '+ data["domname"]+' '+ data["domsrv"]+' '+ data["domip"]+' '+ data["domadmin"]+' '+ data["dompass"]

 elif 'NFS' in data['type']:
  datastr = data['pool']+' '+data['name']+' '+data['size']+' '+data['rootname']+' '+data['rootid']+' '+data['groupname']+' '+data['groupid']+' '+data['ipaddress']+' '+data['Subnet']+' '+data['active']+' '+data['user']+' '+data['owner']+' '+data['user']

 else:
  datastr = data['pool']+' '+data['name']+' '+data['size']+' '+data['groups']+' '+data['ipaddress']+' '+data['Subnet']+' '+data['active']+' '+data['user']+' '+data['owner']+' '+data['user']
 print('#############################')
 with open('/TopStordata/volcreate','a') as f:
    f.write(str(data)+'\n'+'owner '+str(data['owner'])+'\n')
 print(data)
 print(datastr)
 print('###########################')
 if 'ISCSI' not in data['type']:
  # data efficiency of the new volume: both on unless the request says off (the scripts read these two words anywhere on the line)
  datastr += ' compression='+('off' if str(data.get('compression','on')).lower() in ('off','false','no','0') else 'lz4')
  datastr += ' dedup='+('off' if str(data.get('dedup','on')).lower() in ('off','false','no','0') else 'on')
 cmndstring = '/TopStor/VolumeCreate'+data['type']+' '+leaderip+' '+datastr
 #z= cmndstring.split(' ')
 #msg={'req': 'Pumpthis', 'reply':z}
 #sendhost(ownerip, str(msg),'recvreply',myhost)
 postchange(cmndstring,data['owner'])
 return data

def getlogin(token):
 global leaderip
 logindata = get('login',token)[0]
 if logindata == '_1':
  logmsg.sendlog('Lognno0','warning','system2',token)
  return {'response':'baduser'}
 oldtimestamp = logindata[1].split('/')[1]
 user = logindata[0].split('/')[1]
 if int(oldtimestamp) < int(timestamp()):
  dels('login',token)
  loggedusers.pop(token, None)
  logmsg.sendlog('Lognsa0','warning','system',user)
  print('isssss######ss##########33','baduser')
  return 'baduser'
 userdict, token = setlogin(leaderip, myhost,user,'!',token) 
 if token == 0:
  logmsg.sendlog('Lognsa0','warning','system',user)
  return 'baduser'
  print('#################33','baduser')
   
  return 'baduser'
 loggedusers[token] = userdict.copy()
 print('iamokkkkkkkkkkkkkkkkkk')
 return user 

@app.route('/api/v1/logout', methods=['GET','POST'])
def logout():
 global leaderip
 data = request.args.to_dict()
 dels('login',data['token'])
 loggedusers.pop(data['token'], None)
 return data

@app.route('/api/v1/login/test', methods=['GET','POST'])
def testlogin():
 data = request.args.to_dict()
 loginresponse = getlogin(data['token'])
 return { 'response': loginresponse }

@app.route('/api/v1/login', methods=['GET','POST'])
def login():
 data = request.args.to_dict()
 print('#######################')
 print(data)
 print('#######################')
 userdict, token = setlogin(leaderip,myhost, data['user'],data['pass'])
 if token != 0:
  loggedusers[token] = userdict.copy()
  logmsg.sendlog('Lognsu0','info','system',data['user'])
  print('login okkkkkkk',loggedusers)
 else:
  print('login faileddddddddddddddddddd')
  logmsg.sendlog('Lognfa0','error','system',data['user'])
  token = 'baduser'
 return jsonify({'token':token})
@app.route('/api/v1/users/usersauth', methods=['GET','POST'])
@login_required
@audit_command
def usersauth(data):
 global allinfo
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 cmndstring = '/TopStor/Priv.py '+leader+' '+leaderip+' '+myhost+' '+myhostip+' '+data['tochange']+' '+data['auths'].replace(',','/')+' '+data['user']
 postchange(cmndstring)
 return data

@app.route('/api/v1/volumes/config', methods=['GET','POST'])
@login_required
@audit_command
def volumeconfig(data):
 global allinfo, myhost
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 getalltime()
 volume = allinfo['volumes'][data['volume']]
 data['owner'] = volume['host']
 if 'ipaddress' not in data:
   data['ipaddress'] = volume['ipaddress']
 else:
    datatype = ip_context(volume['prot'])

    if data['ipaddress'] != volume['ipaddress']:
        isvu =  int(is_valid_ip(data['ipaddress']))+int(is_unique_ip(data['ipaddress'],datatype))
        if isvu == 0:
            print('ip is valid')
        else:
            if isvu < 100:
                logmsg.sendlog('IPaddrfa','error','system',loggedusers[data['token']]['user'])
            elif isvu > 10 and isvu < 120: 
                logmsg.sendlog('IPadduqfa','error','system',loggedusers[data['token']]['user'])
            else:
                logmsg.sendlog('IPnamuqfa','error','system',loggedusers[data['token']]['user'])
            return data
 if data['owner'] == leader:
  ownerip = leaderip
 else:
  ownerip = allinfo['hosts'][data['owner']]['ipaddress']
 datastr = ''
 data['owner'] = allinfo['hosts'][allinfo['pools'][volume['pool']]['host']]['name']
 if 'ISCSI' in data['type']:
  data['chapuser']='MoatazNegm'
  data['chappas']='MezoAdmin'
  if 'initiators' not in data:
   data['initiators'] = volume['initiators']
  if 'portalport' not in data:
   data['portalport'] = volume['portalport']
  for ele in data:
   volume[ele] = data[ele] 
  datastr = volume['pool']+' '+volume['name']+' '+str(volume['quota'])+' '+data['ipaddress']+' '+str(volume['Subnet'])+' '+data['portalport']+' '+data['initiators']+' '+data['chapuser']+' '+data['chappas']+' '+volume['statusmount']+' '+data['user']+' '+data['owner']+' '+data['user']
 else:

  if 'groups' in data and len(data['groups']) < 1: 
   data['groups'] = 'NoGroup'
  if 'groups' in data:
   data['groups'] = only_everyone(data['groups'])
  for ele in data:
   volume[ele] = data[ele] 
  datastr = volume['pool']+' '+volume['name']+' '+str(volume['quota'])+' '+volume['groups']+' '+volume['ipaddress']+' '+str(volume['Subnet'])+' '+volume['statusmount']+' '+volume['host']+' '+volume['user']
  print('33333333333333333333333333333333333333333333333#############################')
  print('volume',volume)
  print('owner',ownerip)
  print('33333333333333333333333333333333333333333333333#############################')
 cmndstring = '/TopStor/VolumeChange'+data['type']+' '+leaderip+' '+datastr
 postchange(cmndstring,data['owner'])
# z= cmndstring.split(' ')
# msg={'req': 'Pumpthis', 'reply':z}
# sendhost(ownerip, str(msg),'recvreply',myhost)
 #config(data)
 return data

@app.route('/api/v1/user/changepass', methods=['GET','POST'])
@login_required
@audit_command
def changepass(data):
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 data['user'] = data['response']
 print('#############################')
 print(data)
 print('###########################')
 config(leader, leaderip,myhost, data)
 return data



@app.route('/api/v1/hosts/config', methods=['GET','POST'])
@login_required
@audit_command
def hostconfig(data):
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 # Node Config is the privilege 'Error' (usersauth); it used to be only a hint for the UI
 if not has_privilege('Error', data['response']):
  logmsg.sendlog('Priv1004','error','system',data['response'])
  data['error'] = 'noprivilege'
  return data
 if 'ipaddr' in data:
    isvu =  int(is_valid_ip(data['ipaddr']))+int(is_unique_ip(data['ipaddr']))
    if isvu == 0:
        print('ip is valid')
    else:
        if isvu < 100:
            logmsg.sendlog('IPaddrfa','error','system',loggedusers[data['token']]['user'])
        elif isvu > 10 and isvu < 120: 
            logmsg.sendlog('IPadduqfa','error','system',loggedusers[data['token']]['user'])
        else:
            logmsg.sendlog('IPnamuqfa','error','system',loggedusers[data['token']]['user'])
        return data
 if 'cluster' in data:
    dataip = data['cluster'].split('/')[0]   
    isvu =  int(is_valid_ip(dataip))+int(is_unique_ip(dataip))
    if 'ipaddr' in data:
        if dataip == data['ipaddr']:
            isvu = 1
    if isvu == 0:
        print('ip is valid')
    else:
        if isvu == 1:
            logmsg.sendlog('IPaddrclufa','error','system',loggedusers[data['token']]['user'])
        elif isvu < 100:
            logmsg.sendlog('IPaddrfa','error','system',loggedusers[data['token']]['user'])
        elif isvu > 10 and isvu < 120: 
            logmsg.sendlog('IPadduqfa','error','system',loggedusers[data['token']]['user'])
        else:
            logmsg.sendlog('IPnamuqfa','error','system',loggedusers[data['token']]['user'])
        return data

 datastr = ''
 for ele in data:
  datastr += ele+'='+data[ele]+' '
 datastr = datastr[:-1]
 print('#############################')
 print(data)
 print('###########################')

 if 'discovered' in data:
  dleaderip = leaderip
  dleader   = get(dleaderip, 'leader')[0]
  config(dleader, dleaderip, myhost, data)
  return data

 config(leader, leaderip, myhost, data)
 return data

@app.route('/api/v1/hosts/joincluster', methods=['GET','POST'])
@login_required
@audit_command
def hostjoincluster(data):
 global leaderip, myhost
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 data['user'] = data['response']
 if not has_privilege('Error', data['user']):
  logmsg.sendlog('Priv1004','error','system',data['user'])
  data['joinstatus'] = 'noprivilege'
  return data
 # optional changes made together with the join: they travel to the node in tojoin/<name>
 if data.get('ipaddr'):
  isvu = int(is_valid_ip(data['ipaddr']))+int(is_unique_ip(data['ipaddr']))
  if isvu != 0:
   if isvu < 100:
    logmsg.sendlog('IPaddrfa','error','system',data['user'])
   else:
    logmsg.sendlog('IPadduqfa','error','system',data['user'])
   data['joinstatus'] = 'invalid ip'
   return data
  if data.get('ipaddrsubnet') and not (str(data['ipaddrsubnet']).isdigit() and 1 <= int(data['ipaddrsubnet']) <= 32):
   data['joinstatus'] = 'invalid subnet'
   return data
 if data.get('alias') and ('|' in data['alias'] or '=' in data['alias']):
  data['joinstatus'] = 'invalid alias'
  return data
 discover()
 data['leaderip'] = leaderip
 data ['myhost'] = myhost
 Joincluster.do(data) 
 cmndstring = '/TopStor/promserver.sh '+leaderip+' from fapi'
 postchange(cmndstring)
 return data


@app.route('/api/v1/hosts/evacuate', methods=['GET','POST'])
@login_required
@audit_command
def hostevacuate(data):
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 data['user'] = data['response']
 #with open('/root/Evacuate','w') as f:
  #f.write(" ".join([leaderip, myhost, data['name'], data['user']]))
 discover()
 Evacuate.do(leaderip, myhost, data['name'], data['user']) 
 return data

@app.route('/api/v1/volumes/snapshots/snaprollback', methods=['GET','POST'])
@login_required
@audit_command
def volumesnapshotrol(data):
 global allinfo, myhost
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 getalltime()
 volume = allinfo['snapshots'][data['name']]['volume']
 pool = allinfo['snapshots'][data['name']]['pool']
 data['owner'] = allinfo['snapshots'][data['name']]['host']
 ownerip = allinfo['hosts'][data['owner']]['ipaddress']
 cmndstring = "/TopStor/SnapShotRollback "+pool+" "+volume+" "+data['name']+" "+data['user']
 postchange(cmndstring,data['owner'])
# z= cmndstring.split(' ')
# msg={'req': 'Pumpthis', 'reply':z}
 print('##################################')
 print(data)
 print('################################333')
# sendhost(ownerip, str(msg),'recvreply',myhost)
        		 
 return data


@app.route('/api/v1/volumes/snapshots/perioddelete', methods=['GET','POST'])
@login_required
@audit_command
def volumesnapshotperioddel(data):
 global allinfo, myhost
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 getalltime()
 data['owner'] = allinfo['snapperiods'][data['name']]['host']
 ownerip = allinfo['hosts'][data['owner']]['ipaddress']
 cmndstring = "/TopStor/SnapShotPeriodDelete "+leaderip+" "+data['name']+" "+data['user']
 postchange(cmndstring,data['owner'])
# z= cmndstring.split(' ')
# msg={'req': 'Pumpthis', 'reply':z}
# sendhost(ownerip, str(msg),'recvreply',myhost)
 return data  


@app.route('/api/v1/volumes/snapshots/snapshotdel', methods=['GET','POST'])
@login_required
@audit_command
def volumesnapshotdel(data):
 global allinfo, myhost 
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 getalltime()
 volume = allinfo['snapshots'][data['name']]['volume']
 pool = allinfo['snapshots'][data['name']]['pool']
 data['owner'] = allinfo['snapshots'][data['name']]['host']
 ownerip = allinfo['hosts'][data['owner']]['ipaddress']
 cmndstring = "/TopStor/SnapShotDelete "+pool+" "+volume+" "+data['name']+" "+data['user']
 postchange(cmndstring,data['owner'])
# z= cmndstring.split(' ')
# msg={'req': 'Pumpthis', 'reply':z}
 print('##################################')
 print(data)
 print('################################333')
# sendhost(ownerip, str(msg),'recvreply',myhost)
        		 
 return data

@app.route('/api/v1/volumes/volumeactive', methods=['GET','POST'])
@login_required
@audit_command
def volumeactive(data):
 global allinfo, myhost
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 pool = allinfo['volumes'][data['name']]['pool']
 prot = allinfo['volumes'][data['name']]['prot']
 data['owner'] = allinfo['volumes'][data['name']]['host']
 ownerip = allinfo['hosts'][data['owner']]['ipaddress']
 cmndstring = "/TopStor/Volumeactive.py "+leaderip+" "+myhost+" "+pool+" "+data['name']+" "+prot+" "+data['active']+" "+data['user']
 postchange(cmndstring,data['owner'])
 print('##################################')
 print('volumeactive',cmndstring)
 print('##################################')
# z= cmndstring.split(' ')
# msg={'req': 'Pumpthis', 'reply':z}
# sendhost(ownerip, str(msg),'recvreply',myhost)
 return data



@app.route('/api/v1/volumes/volumedel', methods=['GET','POST'])
@login_required
@audit_command
def volumedel(data):
 global allinfo, myhost
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 getalltime()
 pool = allinfo['volumes'][data['name']]['pool']
 data['owner'] = allinfo['volumes'][data['name']]['host']
 ownerip = allinfo['hosts'][data['owner']]['ipaddress']
 cmndstring = "/TopStor/VolumeDelete"+data['type']+" "+leaderip+" "+pool+" "+data['name']+" "+data['type']+" "+data['user']
 postchange(cmndstring,data['owner'])
# z= cmndstring.split(' ')
# msg={'req': 'Pumpthis', 'reply':z}
 print('##################################')
 print(data)
 print('################################333')
# sendhost(ownerip, str(msg),'recvreply',myhost)
        		 
 return data

@app.route('/api/v1/groups/groupdel', methods=['GET','POST'])
@login_required
@audit_command
def groupdel(data):
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 if data.get('name') == EVERYONE:
  data['error'] = 'everyonefixed'
  return data
 cmndstring = '/TopStor/UnixDelGroup '+leaderip+' '+data.get('name')+' '+data['user'] 
 postchange(cmndstring)
 return data

@app.route('/api/v1/partners/partnerdel', methods=['GET','POST'])
@login_required
@audit_command
def partnerdel(data):
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 cmndstring = '/TopStor/PartnerDel.py '+data.get('name')+' no '+data['user']
 postchange(cmndstring)
 return data



@app.route('/api/v1/users/userdel', methods=['GET','POST'])
@login_required
@audit_command
def userdel(data):
 global allinfo
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 if data['tenant'] == 'Cluster':
    cmndstring = '/TopStor/UnixDelUser '+leaderip+' '+data.get('name')+' '+data['user']
    postchange(cmndstring)
    # the home folder of the user goes with the user (its dataset is <user>_<id>, protocol HOME)
    if data.get('name') not in ('admin', 'Everyone', 'NoUser'):
     getalltime()
     for volname, vol in list(allinfo['volumes'].items()):
      if vol.get('prot') in ('HOME', 'HOMEE') and volname.rsplit('_',1)[0] == data.get('name'):
       postchange('/TopStor/VolumeDeleteHOME '+leaderip+' '+vol['pool']+' '+volname+' HOME '+data['user'], vol['host'])
 else:
    TenantDelUser(data)
 return data

@app.route('/api/v1/groups/UnixAddgroup', methods=['GET','POST'])
@login_required
@audit_command
def UnixAddGroup(data):
 global allusers, allgroups
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 if data.get('name') == EVERYONE:
  data['error'] = 'everyonefixed'
  return data
 usrstr = ''
 usrs = data['users']
 if len(usrs) < 1:
  usrstr = 'NoUser'
 else:
  for usr in usrs.split(','):
   for suser in allusers:
    if str(suser['id']) == str(usr):
     usrstr += suser['name']+',' 
  usrstr = usrstr[:-1]

 if int(is_unique_name(data['name']))==1000:
    logmsg.sendlog('IPnamuqfa','error','system',loggedusers[data['token']]['user'])
    return data
 cmndstring = '/TopStor/UnixAddGroup '+leaderip+' '+data['name']+' '+' users'+usrstr+' '+data['user']
 postchange(cmndstring)
 return data

PARTNER_PORT_MIN = 3001
PARTNER_PORT_MAX = 8000

def partner_ports_used():
 # ports already given to a partner entry:  Partner/<alias>_<type> = <ip>/<type>/<port>/<phrase>
 used = set()
 try:
  for partner in etcdgetjson(leaderip,'Partner/','--prefix'):
   split = partner["prop"].split('/')
   if len(split) > 2:
    used.add(str(split[2]).strip())
 except Exception as e:
  print('partner_ports_used:', e)
 return used

def port_listening(port):
 # a listener of this node holds the port (ss), or it cannot be bound (bind test)
 try:
  out = subprocess.run(['ss','-ltnH'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10).stdout.decode('utf-8','replace')
  for line in out.split('\n'):
   cols = line.split()
   if len(cols) > 3 and cols[3].rsplit(':',1)[-1] == str(port):
    return True
 except Exception as e:
  print('port_listening ss:', e)
 sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
 try:
  sock.bind(('0.0.0.0', int(port)))
  return False
 except OSError:
  return True
 finally:
  sock.close()

def partner_port_free(port, used=None):
 try:
  port = int(str(port).strip())
 except (TypeError, ValueError):
  return False
 if port < PARTNER_PORT_MIN or port > PARTNER_PORT_MAX:
  return False
 if used is None:
  used = partner_ports_used()
 return str(port) not in used and not port_listening(port)

@app.route('/api/v1/partners/freeport', methods=['GET'])
@login_required
@audit_command
def api_partners_freeport(data):
 # a random free port in 3001..8000 for a new partner; no side effects
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 import random
 used = partner_ports_used()
 for _ in range(50):
  port = random.randint(PARTNER_PORT_MIN, PARTNER_PORT_MAX)
  if partner_port_free(port, used):
   return {'port': port}
 return {'port': 0, 'response': 'select another port'}

@app.route('/api/v1/partners/AddPartner', methods=['GET','POST'])
@login_required
@audit_command
def AddPartner(data):
 if 'baduser' in data['response']:
  return {'response': 'baduser'} 
 # the command string is split on ' ' (postchange), so whitespace inside an argument shifts or corrupts the arguments; a phrase
 # with a trailing newline ended up in the sync key name and broke etcd sync on both clusters (QC test 22 step 16.5).
 # Strip the values and refuse empty ones or whitespace inside them (and '/' in ip, type and phrase: the Partner value is
 # 'ip/type/port/phrase'; '/' in the alias is mapped to ':::' by PartnerAdd.py), like the port guard below.
 for k in ('ip','alias','type','port','pass'):
  if k in data and isinstance(data[k], str):
   data[k] = data[k].strip()
 for k in ('ip','alias','type','pass'):
  v = str(data.get(k,''))
  if v == '' or any(c.isspace() for c in v) or (k != 'alias' and '/' in v):
   logmsg.sendlog('Partnargfa','error','system',loggedusers[data['token']]['user'])
   return 'invalid '+('phrase' if k == 'pass' else k)
 isvu =  int(is_valid_ip(data['ip']))+int(is_unique_ip(data['ip']))
 if isvu == 0:
        print('ip is valid')
 else:
        if isvu < 100:
            logmsg.sendlog('IPaddrfa','error','system',loggedusers[data['token']]['user'])
        elif isvu > 10 and isvu < 120: 
            logmsg.sendlog('IPadduqfa','error','system',loggedusers[data['token']]['user'])
        else:
            logmsg.sendlog('IPnamuqfa','error','system',loggedusers[data['token']]['user'])
        return data

 # the port must be 3001-8000, not used by another partner and not listened on by this node: nothing is authorized otherwise
 if 'port' not in data or not partner_port_free(data['port']):
  logmsg.sendlog('Partnportfa','error','system',loggedusers[data['token']]['user'])
  return 'select another port'

 print('##########################33333')
 print({k: v for k, v in data.items() if k != 'pass'})
 print('##########################33333')
 cmdstring = '/TopStor/PartnerAdd.py '+data['ip']+' '+data['alias']+' '+data['type']+' '+data['port']+' '+data['pass']+' '+data['user'] + ' init'
 postchange(cmdstring)
 return data


@app.route('/api/v1/tenant/adduser', methods=['GET','POST'])
@login_required
@audit_command
def TenantAddUser(data):
 global allgroups, leaderip
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 volinfo = get('vol',data['tenant'])[0]
 data['owner']= 'leader' 
 if 'NFS' in volinfo[0]:
    data['owner'] = volinfo[0].split('/')[2]
    pool = volinfo[0].split('/')[3]
    grp = data['groups']
    if str(grp) != '0' and str(grp) != volinfo[1].split('_u_')[2].split(':')[0]:
        grp = 'NoGroup'
    cmndstring = '/TopStor/TenantAddUser '+leaderip+' '+data['response']+' '+pool+' '+data['tenant']+' '+data['name']+' '+data['userid']+' '+grp
 postchange(cmndstring,data['owner'])
 return data
 
def TenantDelUser(data):
 global allgroups, leaderip
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 volinfo = get('vol',data['tenant'])[0]
 data['owner'] = 'leader'
 if 'NFS' in volinfo[0]:
    data['owner'] = volinfo[0].split('/')[2]
    pool = volinfo[0].split('/')[3]
    cmndstring = '/TopStor/TenantDelUser '+leaderip+' '+data['response']+' '+pool+' '+data['tenant']+' '+data['name']
 postchange(cmndstring,data['owner'])
 return data



@app.route('/api/v1/users/UnixAddUser', methods=['GET','POST'])
@login_required
@audit_command
def UnixAddUser(data):
 global allgroups, leaderip
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 # Guards (uservalid.py): name of 3-32 letters/digits/-, not a system account, a real password, and no second
 # user with exactly the same name.  The answer says what happened in 'adduser': 'accepted' or 'rejected: <why>'.
 for field in ('name', 'Password', 'Volpool'):
  if field not in data:
   data['adduser'] = 'rejected: the field '+field+' is missing'
   return data
 # the values as they were sent: login_required removes every blank from the parameters, so a name or a password
 # with a blank would otherwise be accepted as a different one (and the user could not log in with what was typed)
 rawname = request.args.get('name', data.get('name'))
 code, reason = check_new_user_coded(rawname, request.args.get('Password', data.get('Password')))
 if reason:
  # every rejection is reported through logmsg, with a message that says what was wrong
  logmsg.sendlog(code,'error',loggedusers[data['token']]['user'],logname(rawname))
  data['adduser'] = 'rejected: '+reason
  data['response'] = 'rejected'
  return data
 if str(get('usersinfo/'+data['name'])[0]) not in ('_1', '-1', '', 'None'):
  logmsg.sendlog('Unlin1021uu','error',loggedusers[data['token']]['user'],data['name'])
  data['adduser'] = 'rejected: a user with this name already exists'
  data['response'] = 'rejected'
  return data
 pool = data['Volpool']
 if 'NoHome' in data['Volpool'] or '-' in data['Volpool']:
  pool = 'NoHome'
 else:
    isvu =  int(is_valid_ip(data['HomeAddress']))+int(is_unique_ip(data['HomeAddress'],'HOME'))
    if isvu == 0:
        print('ip is valid')
    else:
        if isvu < 100:
            logmsg.sendlog('IPaddrfa','error','system',loggedusers[data['token']]['user'])
        elif isvu > 10 and isvu < 120: 
            logmsg.sendlog('IPadduqfa','error','system',loggedusers[data['token']]['user'])
        else:
            logmsg.sendlog('IPnamuqfa','error','system',loggedusers[data['token']]['user'])
        return data


 if int(is_unique_name(data['name']))==1000:
    logmsg.sendlog('IPnamuqfa','error','system',loggedusers[data['token']]['user'])
    data['adduser'] = 'rejected: the name is already used by a user, a group or a volume'
    return data
    
 grps = data.get('groups') or ''
 groupstr = ''
 allgroups = getgroups()
 if len(grps) < 1:
  groupstr = 'NoGroup'
 else:
  for grp in grps.split(','):
   groupstr += allgroups[int(grp)][0]+','
  groupstr = groupstr[:-1]
 groupstr = with_everyone(groupstr)
 cmndstring = '/TopStor/UnixAddUser '+leaderip+' '+data.get('name')+' '+pool+' groups'+groupstr+' ' \
     +data.get('Password')+' '+data.get('Volsize')+'G '+data.get('HomeAddress')+' '+data.get('HomeSubnet')+' hoststub'+' '+data['user']
 print('*************************************************************************')
 print(cmndstring)
 print('*************************************************************************')
 postchange(cmndstring)
 data['adduser'] = 'accepted'
 return data 

@app.route('/api/v1/volumes/grouplist', methods=['GET'])
@login_required
@audit_command
def api_volumes_groupslist(data):
 global allgroups, allusers
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 thegroup = [] 
 api_users_userslist(data)
 for group in allgroups:
  groupusers = []
  for user in allusers:
   if user['name'] in str(group[2]):
    groupusers.append(user['id'])
  if len(groupusers) < 1:
   groupusers=["NoUser"]
  thegroup.append({'text':group[0], 'id':group[1], 'users':groupusers})
 return jsonify({'results':thegroup})




@app.route('/api/v1/groups/grouplist', methods=['GET'])
@login_required
@audit_command
def api_groups_groupslist(data):
 global allgroups, allusers
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 thegroup = [] 
 api_users_userslist(data)
 for group in allgroups:
 # if group[0] == 'Everyone':
 #  continue

  groupusers = []
  for user in allusers:
   grpusrs = str(group[2]).split(',')
   for grp  in grpusrs:
    if user['name'] == grp:
     groupusers.append(user['id'])
#   if user['name'] in str(group[2]) :
#    groupusers.append(user['id'])
  if len(groupusers) < 1:
   groupusers=["NoUser"]
  thegroup.append({'name':group[0], 'id':group[1], 'users':groupusers})
 return jsonify({'allgroups':thegroup})

@app.route('/api/v1/users/userauths', methods=['GET'])
@login_required
@audit_command
def userauths(data):
 global allgroups, allusers, leaderip
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 if  data['username'] == 'admin':
  return {'auths':'true','response':data['response']}
 userlst = etcdgetjson(leaderip,'usersinfo','--prefix')
 for user in userlst:
  username = user['name'].replace('usersinfo/','')
  if username == data['username']:
   priv = '/'.join(user['prop'].split('/')[4:])
   break
 return jsonify({'auths':priv, 'response':data['response']})

@app.route('/api/v1/partners/partnerlist', methods=['GET'])
@login_required
@audit_command
def api_partners_userslist(data):
 global leaderip
 if 'baduser' in data['response']:
  return {'response': 'baduser'}
 allpartners=[]
 partnerlst = etcdgetjson(leaderip,'Partner/','--prefix')
 for partner in partnerlst:
  alias =  partner["name"].split('/')[1] 
  split = partner["prop"].split('/') 
  allpartners.append({'alias': alias, "ip":split[0], "type":split[1], "port":split[2]})
 return { "allpartners":allpartners }

@app.route('/api/v1/users/userlist', methods=['GET'])
@login_required
@audit_command
def api_users_userslist(data):
 global allgroups, allusers, leaderip
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 if 'tenant' not in data:
    data['tenant'] = 'Cluster'
 alldict = dict()
 allusers = []
 if 'Cluster' not in data['tenant']:
    userlst = get('tenant',data['tenant']+'/users')[0][1].split('/')
    grplst = get('tenant',data['tenant']+'/group')[0][1].split('/')[1]
    grpusrs = grplst.split(':')[-1]
    group = grplst.split(':')[0]
    uid=0
    for user in userlst:
        username = user.split(':')[0]
        if username == '':
            continue
        userid = user.split(':')[1]
        if username in grplst:
            grp = [0] 
        else:
            grp = 'NoGroup'
        allusers.append({"name":username, 'id':uid, 'userid':userid, "pool":'na', "size":'na', "groups":grp, 'priv':'na'})
        uid += 1
    alldict['allusers'] = allusers
    alldict['allgroups'] = [[group,0,grpusrs]]
    alldict['usersnohome'] = [] 
    return jsonify(alldict)
 userlst = etcdgetjson(leaderip,'usersinfo','--prefix')
 allgroups = getgroups()
 userdict = dict()
 for group in allgroups:
  groupid = group[1]
  grpusers = group[2].split(',')
  for grpuser in grpusers:
   if grpuser not in userdict:
    userdict[grpuser] = []
   userdict[grpuser].append(str(groupid))
 usersnohome = []
 nohomeid = 0
 uid = 0
 for user in userlst:
  username = user['name'].replace('usersinfo/','')
  usersize = user['prop'].split('/')[3]
  userpool = user['prop'].split('/')[1]
  priv = '/'.join(user['prop'].split('/')[4:])
  if username not in userdict:
   groups = ['NoGroup']
  else:
   groups = userdict[username]
  allusers.append({"name":username, 'id':uid, "pool":userpool, "size":usersize, "groups":groups, 'priv':priv})
  uid += 1
  if 'NoHome' in userpool:
   usersnohome.append({ 'id':nohomeid, 'text': username })
   nohomeid += 1 
 alldict['allusers'] = allusers
 alldict['allgroups'] = allgroups
 alldict['usersnohome'] = usersnohome
 return jsonify(alldict)

@app.route('/api/v1/groups/userlist', methods=['GET'])
@login_required
@audit_command
def api_groups_userlist(data):
 global allusers
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 usr = []
 api_users_userslist(data)
 for user in allusers:
  usr.append({'id':user['id'],'text':user['name']})
 return jsonify({'results':usr})


@app.route('/api/v1/users/grouplist', methods=['GET'])
@login_required
@audit_command
def api_users_grouplist(data):
 global allgroups
 if 'baduser' in data['response']:
      return {'response': 'baduser'}
 grp = []
 if 'Cluster' not in data['tenant']:
    allgroups = [get('tenant',data['tenant']+'/group')[0][1].split('/')[1].split(':')[0]]
    for group in allgroups:
        grp.append({'id':0,'text':group})
 else:
    allgroups = getgroups()
    for group in allgroups:
        grp.append({'id':group[1],'text':group[0]})
 return jsonify({'results':grp, 'response':data['response']})


@app.route('/api/v1/pools/actionOnDisk', methods=['GET','POST'])
@login_required
@audit_command
def offlineOrOnlineDisk(data):
    global allinfo, myhost, leaderip
    if 'baduser' in data['response']:
      return {'response': 'baduser'}
    getalltime()
    action = data['action']
    pool = data['pool']
    disk = data['actualdisk'] # "scsi-*" format. 
    #disk = data['actualdisk'] # "sd*" format.
    data['owner'] = allinfo['pools'][pool]['host']
    ownerip = allinfo['hosts'][data['owner']]['ipaddress']
    datastr = myhost + ' ' + data['user'] + ' ' + leaderip + ' ' + action + ' ' + pool + ' ' + disk
    cmndstring = 'python /TopStor/actionOnDisk.py ' + datastr
    postchange(cmndstring,data['owner'])
#    z = cmdline.split(' ')
#    msg = {'req': 'Pumpthis', 'reply':z}
#    sendhost(ownerip, str(msg),'recvreply',myhost)
    return data

@app.route('/api/v1/hosts/getConfig', methods=['GET','POST'])
@login_required
@audit_command
def getNodeConfigFile(data):
    global leaderip, myhost
    if 'baduser' in data['response']:
      return {'response': 'baduser'}
    nodeName = data["nodeName"]
    getConfig(leaderip, nodeName)
    downloadConfig(leaderip, myhost)
    file_path = "/TopStordata/config_" + nodeName + ".txt"
    return send_file(file_path, mimetype='text/plain', as_attachment=True)

@app.route('/api/v1/hosts/getAllConfig', methods=['GET','POST'])
@login_required
@audit_command
def getAllConfigFiles(data):
    global leaderip, readyhosts
    if 'baduser' in data['response']:
      return {'response': 'baduser'}
    hostsready()
    configFiles = []
    zipfilePath = "/TopStordata/All_Configs.zip"
    getConfig(leaderip, myhost)
    downloadConfig(leaderip, myhost)
    for host in readyhosts:
        nodeName = host["name"]
        nodeip = host['ip']
        filePath = "/TopStordata/config_" + nodeName + ".txt"
        configFiles.append((filePath, 'config_'+nodeName + ".txt"))
    with zipfile.ZipFile(zipfilePath, "w") as zipF:
        for file in configFiles:
            zipF.write(file[0], file[1] ,compress_type = zipfile.ZIP_DEFLATED)
    return send_file(zipfilePath, as_attachment=True)

@app.route('/api/v1/software/update', methods=['GET','POST'])
@login_required
@audit_command
def updateSoftware(data):
    if data.get('response') == 'baduser':
        return {'response': 'baduser'}

    source_type = data.get('source-type')
    source = data.get('source')
    location = data.get('location')
    version = data.get('version')

    command = ['./downloadSoftwareUpdate.sh', f'--source-type {source_type}', f'--source {source}']

    if version:
        command.append(f'--version {version}')

    if source_type == 'cifs':
        username = data.get('username')
        password = data.get('password')
        command.extend([
            f'--location {location}',
            f'--username {username}',
            f'--password {password}'
        ])
    elif source_type == 'nfs':
        command.append(f'--location {location}')

    command_string = ' '.join(command)
    postchange(command_string, 'leader')

    return {'data': command_string}

@app.route('/api/v1/software/localFileUpdate', methods=['GET','POST'])
@login_required
@audit_command
def localFileUpdate(data):
    if 'baduser' in data['response']:
      return {'response': 'baduser'}
    
    uploaded_file = request.files['file']
    if uploaded_file.filename != '':
        dirPath = '/TopStordata'
        isExist = os.path.exists(dirPath)
        if not isExist:
            os.makedirs(dirPath)
        filename =  uploaded_file.filename.replace(' ', '')
        filePath = os.path.join(dirPath, filename)
        uploaded_file.save(filePath)
        return {"data": 'success'}
    return {"data": uploaded_file.filename}


@app.route('/api/v1/telemetry/heartbeat', methods=['POST', 'GET'])
def telemetry_heartbeat():
    # Write the current epoch time to a file
    try:
        with open('/tmp/ui_active_heartbeat', 'w') as f:
            f.write(str(int(time.time())))
        return {"status": "boost_active"}, 200
    except Exception as e:
        return {"error": str(e)}, 500


@app.route('/api/v1/info/summary', methods=['GET', 'POST'])
def get_service_summary():
    global leaderip # Use the globally defined leaderip from fapi.py
    
    try:
        # 1. Users and Groups (JSON data)
        # fapi.py native approach: etcdgetjson(leaderip, 'usersinfo', '--prefix')
        users_data = etcdgetjson(leaderip, 'usersinfo', '--prefix')
        users_count = len(users_data) if isinstance(users_data, list) else 0

        groups_data = etcdgetjson(leaderip, 'usersigroup', '--prefix')
        groups_count = len(groups_data) if isinstance(groups_data, list) else 0

        # 2. Pools (Standard data)
        # fapi.py native approach: get('pools/','--prefix')
        pools_data = get('pools/', '--prefix')
        pools_count = len(pools_data) if isinstance(pools_data, list) else 0

        # 3. Categorize Volumes (Standard data)
        # fapi.py native approach: get('vol', '--prefix')
        cifs_count = 0
        nfs_count = 0
        iscsi_count = 0

        volumes_data = get('vol', '--prefix')
        if isinstance(volumes_data, list):
            for vol in volumes_data:
                # vol is a tuple e.g., ('vol/NFS/poolname/volname', 'active')
                if isinstance(vol, (list, tuple)) and len(vol) > 0:
                    key = str(vol[0]).upper()
                    if '/CIFS' in key:
                        cifs_count += 1
                    elif '/NFS' in key:
                        nfs_count += 1
                    elif '/ISCSI' in key:
                        iscsi_count += 1

        # 4. Return the exact JSON structure React expects
        return jsonify({
            "users": users_count,
            "groups": groups_count,
            "pools": pools_count,
            "volumes": {
                "cifs": cifs_count,
                "nfs": nfs_count,
                "iscsi": iscsi_count
            }
        }), 200

    except Exception as e:
        print(f"Telemetry Summary Error: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500
  
leaderip =0 
myhost=0
if __name__=='__main__':
    #leader = sys.argv[2]
    #leaderip = sys.argv[1]
    # start-up log: one line per step with the seconds since the process started (/TopStordata/fapistart.log,
    # rewritten at every start).  It answers "why did the API take N seconds to come back after a take over".
    import time as _t
    _t0 = _FAPI_T0
    def _mark(step):
        try:
            with open('/TopStordata/fapistart.log', 'a') as f:
                f.write('%s +%.1fs %s\n' % (_t.strftime('%H:%M:%S', _t.gmtime()), _t.time()-_t0, step))
        except Exception:
            pass
    try:
        open('/TopStordata/fapistart.log', 'w').close()
    except Exception:
        pass
    _mark('imports done (interpreter started at '+_t.strftime('%H:%M:%S', _t.gmtime(_FAPI_T0))+')')
    leaderip = get('leaderip')[0]
    myhost = get('clusternode')[0]
    myhostip = get('clusternodeip')[0]
    leader = get('leader')[0]
    _mark('leaderip, clusternode, clusternodeip, leader read from etcd')
    logmsg.initlog(leaderip,myhost)
    initallphy(leaderip)
    _mark('logmsg.initlog + initallphy')
    #myhost = sys.argv[2]
    getalltime()
    _mark('getalltime (disks, pools, volumes) -- starting the web server now')
   #myhostip = sys.argv[5]
    app.run(host="0.0.0.0", port=5001)


