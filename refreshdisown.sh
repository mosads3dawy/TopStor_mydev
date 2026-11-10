#!/usr/bin/sh
[ -f /TopStor/flavor.sh ] && . /TopStor/flavor.sh
fnkillall () {
process=(`ps -ef | grep $1 | grep -v color | grep -v grep | awk '{print $2}'`)
for proc in "${process[@]}"; do
 echo proc $proc
 kill -9 $proc 2>/dev/null
done
}
leader=`docker exec etcdclient /TopStor/etcdgetlocal.py leader`
leaderip=`docker exec etcdclient /TopStor/etcdgetlocal.py leaderip`
myhost=`docker exec etcdclient /TopStor/etcdgetlocal.py clusternode`
myhostip=`docker exec etcdclient /TopStor/etcdgetlocal.py clusternodeip`
echo $leader | grep $myhost
if [ $? -eq 0 ];
then
	etcdip=$leaderip
else
	etcdip=$myhostip
fi
cujobs=(`echo diskreflooper zpooltoimportlooper iscsiwatchdog zfsping receivereplylooper topstorrecvreply checksyncs syncrequestlooper selectsparelooper VolumeChecklooper croncalllooper selectimportlooper retryvolumedeletelooper zfstelemetrylooper`)
declare  -A cmdcjobs
cmdcjobs['iscsiwatchdog']="/TopStor/iscsiwatchdog.sh" 
is_container 2>/dev/null && cmdcjobs['iscsiwatchdoglooper']="/TopStor/iscsiwatchdoglooper.sh" 
cmdcjobs['zfsping']="/pace/zfsping.py"
cmdcjobs['topstorrecvreply']="echo"
cmdcjobs['receivereplylooper']="/TopStor/receivereplylooper.sh"
cmdcjobs['syncrequestlooper']="/pace/syncrequestlooper.sh"
cmdcjobs['selectsparelooper']="/pace/selectsparelooper.sh"
cmdcjobs['VolumeChecklooper']="/pace/VolumeChecklooper.sh"
cmdcjobs['diskreflooper']="/pace/diskreflooper.sh"
cmdcjobs['zpooltoimportlooper']="/pace/zpooltoimportlooper.sh"
cmdcjobs['croncalllooper']="/pace/croncalllooper.sh"
cmdcjobs['checksyncs']="echo"
cmdcjobs['retryvolumedeletelooper']="/pace/retryvolumedeletelooper.sh"
cmdcjobs['selectimportlooper']="/pace/selectimportlooper.sh"
cmdcjobs['zfstelemetrylooper']="/pace/zfstelemetrylooper.sh"

# alive <name>: processes whose command line holds <name>, zombies and this check itself left out
alive() { ps -eo stat=,args= | awk -v j="$1" '$1 !~ /^Z/ && index($0, j) && $0 !~ /awk -v j=/' | wc -l; }

while true;
do
 # the request: refreshdisown/<myhost> = yes (written by the node that took over, by docker_setup.sh ...)
 docker exec etcdclient /TopStor/etcdgetlocal.py refreshdisown/$myhost | grep -q yes
 if [ $? -eq 0 ];
 then
  echo 'SSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSstart'
  docker exec etcdclient /TopStor/etcdput.py etcd refreshdisown/$myhost 1
  cjobs=("${cujobs[@]}")
  tries=0
  # Restart every job: kill it, wait until it is really gone, start it again.  A job that is still alive after the
  # wait is retried in the next round.  (The old loop counted the processes right after "kill -9" -- which is
  # asynchronous --, skipped the job when one was still listed, and never retried it because its list of jobs left
  # was emptied by a "grep -v" on one joined line; the flag then stayed above 0 and the loop spun for ever, so
  # after a take over zfsping could be missing and no later refresh request was ever seen.)
  while [ ${#cjobs[@]} -gt 0 ] && [ $tries -lt 20 ];
  do
   tries=$((tries+1))
   newjobs=()
   for job in "${cjobs[@]}";
   do
    echo '###########################################'
    echo $job
    fnkillall $job
    w=0; while [ $w -lt 20 ] && [ `alive $job` -ne 0 ]; do sleep 0.5; w=$((w+1)); done
    if [ `alive $job` -eq 0 ];
    then
     # the leader may have changed since this refresh began: read it again for every start
     leader=`docker exec etcdclient /TopStor/etcdgetlocal.py leader`
     leaderip=`docker exec etcdclient /TopStor/etcdgetlocal.py leaderip`
     cmd=${cmdcjobs[$job]}
     echo $cmd $leaderip $myhost $leader $myhostip\& disown	>> /root/refreshtemp
     $cmd $leaderip $myhost $leader $myhostip >/dev/null & disown
    else
     echo "$job is still alive after the kill, retried in the next round"
     newjobs+=("$job")
    fi
   done
   cjobs=("${newjobs[@]}")
   [ ${#cjobs[@]} -gt 0 ] && sleep 1
  done
  [ ${#cjobs[@]} -gt 0 ] && echo "jobs NOT restarted after $tries rounds: ${cjobs[@]}" >> /root/refreshtemp
  docker exec etcdclient /TopStor/etcdput.py etcd refreshdisown/$myhost 0
  echo 'SSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSstop'
 fi
 sleep 2
done
