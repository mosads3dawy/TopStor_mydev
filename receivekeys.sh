#!/bin/sh
echo $@ > /root/receivekeys
partner=`echo $@ | awk '{print $1}'`
partnerip=`echo $@ | awk '{print $2}'`
clusterip=`echo $@ | awk '{print $3}'`
port=`echo $@ | awk '{print $4}'`
ppass=`echo $@ | awk '{print $5}'`
keys=`echo $@ | awk '{print $6}'`

leader=`docker exec etcdclient /TopStor/etcdgetlocal.py leader `
leaderip=`docker exec etcdclient /TopStor/etcdgetlocal.py leaderip `
myhost=`docker exec etcdclient /TopStor/etcdgetlocal.py clusternode `
myhostip=`docker exec etcdclient /TopStor/etcdgetlocal.py clusternodeip `
/TopStor/etcdget.py $leaderip Partner $clusterip | grep $ppass 
if [ $? -eq 0 ];
then
 echo it is okok >> /root/receivekeys
 /TopStor/etcdput.py $leaderip nodesender/${clusterip}/$partnerip/$myhostip $partner
 echo $myhost | grep $leader
 if [ $? -ne 0 ];
 then
 	/TopStor/etcdput.py $myhostip nodesender/${clusterip}/$partnerip $partner   2>/dev/null
 fi
 # a node that never used ssh as root has no /root/.ssh: without it the key below was never written and the sender stayed locked out
 mkdir -p /root/.ssh
 chmod 700 /root/.ssh
 [ -f /root/.ssh/authorized_keys ] || : > /root/.ssh/authorized_keys
 authkeys=`cat /root/.ssh/authorized_keys | grep -v $partner`
 echo "$authkeys" > /root/.ssh/authorized_keys
 echo $keys | sed 's/\_spc\_/ /g' >> /root/.ssh/authorized_keys
 sshd=`cat /etc/ssh/sshd_config | grep -v "Port 22" | grep -v $port`
 echo -e "$sshd" > /etc/ssh/sshd_config
 echo -e "Port $port \r"  >> /etc/ssh/sshd_config
 echo -e "Port 22 \r" >> /etc/ssh/sshd_config
 firewall-cmd --permanent --add-port=$port/udp
 firewall-cmd --permanent --add-port=$port/tcp
 firewall-cmd --reload
 systemctl restart sshd
 # container flavour: the systemctl wrapper does not restart sshd, so the new Port line was never listened on and the
 # sending cluster got 'Connection refused' on the replication port; SIGHUP makes sshd re-read its configuration
 [ -f /TopStor/flavor.sh ] && . /TopStor/flavor.sh
 is_container 2>/dev/null && pkill -HUP -x sshd
 chmod 004 /root/.ssh/authorized_keys
fi
