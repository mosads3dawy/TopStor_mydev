#!/bin/sh
[ -f /TopStor/flavor.sh ] && . /TopStor/flavor.sh
myclusterf='/topstorwebetc/mycluster'
mynodef='/topstorwebetc/mynode'
myhost=`echo $@ | awk '{print $1}'`
myhostip=`echo $@ | awk '{print $2}'`
leaderip=`echo $@ | awk '{print $3}'`
myclusterip=$leaderip
leader=$myhost
nmcli conn up mynode
#zpool export -a
eth1='enp0s8'
eth2='enp0s8'
mynodedev=$eth1
myclusterdev=$eth1
data1dev=$eth2
data2dev=$eth2
setenforce 0
#targetcli clearconfig confirm=true
nmcli conn mod cmynode +ipv4.addresses $myclusterip 
nmcli conn up cmynode
# Tell the network that the cluster ip lives here now (gratuitous ARP).  Without it a client that talked to the
# old leader keeps the cluster ip on the dead node's MAC until its own ARP entry ages (measured: 34-47 s, although
# this node served the UI and the API after ~19 s).  NetworkManager's own announcement is not enough: it goes out on
# the connection's device only, and right after "conn up" the bond may still be without its port (in the container
# flavour bond0 has no carrier at all and the address is answered on eth0).  So: every interface with a connected
# route to the cluster ip, a few times, in the background.
announce_clusterip() {
	for garpdev in `ip -o -4 route show to match $myclusterip 2>/dev/null | grep -v '^default' | sed -n 's/.* dev \([^ ]*\).*/\1/p' | sort -u`
	do
		arping -U -c 1 -I $garpdev -s $myclusterip $myclusterip >/dev/null 2>&1
	done
}
( announce_clusterip; sleep 1; announce_clusterip; sleep 2; announce_clusterip; sleep 4; announce_clusterip ) >/dev/null 2>&1 </dev/null &
myclusterip=$leaderip
pkill iscsiwatch
pkill topstorrecvreply
echo nameserver 10.11.12.7 >  /root/gitrepo/resolv.conf
rm -rf /TopStordata/etcd.sh
cp /TopStor/discovery.sh /TopStordata/etcd.sh
sed -i 's/SLEEP/sleep 10/g' /TopStordata/etcd.sh
echo starting etcd
docker run -itd --rm --name etcd --hostname etcd -v /etc/localtime:/etc/localtime:ro -v /root/gitrepo/resolv.conf:/etc/resolv.conf -p $etcd:2379:2379 -v /TopStor/:/TopStor -v /root/etcddata:/default.etcd  -v /TopStordata/etcd.sh:/runme.sh --net ${DOCKER_NET:-bridge0} moataznegm/quickstor:etcd
newip=`docker exec intdns nslookup etcd | grep Address | grep -v 127 | awk '{print $2}'`
echo newip=$newip
docker rm -f etcd 
rm -rf /TopStordata/etcd.sh
cp /TopStor/discovery.sh /TopStordata/etcd.sh
sed -i 's/SLEEP//g' /TopStordata/etcd.sh
sed -i "s/ETCDIP/$newip/g" /TopStordata/etcd.sh
docker run -itd --rm --name etcd --hostname etcd -v /etc/localtime:/etc/localtime:ro -v /root/gitrepo/resolv.conf:/etc/resolv.conf -p $myclusterip:2379:2379 -v /TopStor/:/TopStor -v /root/etcddata:/default.etcd  -v /TopStordata/etcd.sh:/runme.sh --net ${DOCKER_NET:-bridge0} moataznegm/quickstor:etcd
started=0
waited=0
while [ $started -eq 0 ];
do
		echo waiting etcd to settle 
		docker logs etcd | grep 'successfully notified init daemon' 
		if [ $? -eq 0 ];
	 	then
			started=1
	 	else
		 	sleep 1
			waited=$((waited+1))
			# never wait for ever: the take over must go on (or fail visibly) instead of hanging the heartbeat
			if [ $waited -ge 180 ];
			then
				echo etcd did not settle in 180 seconds .... going on
				echo `date` docker_primary.sh: etcd did not settle in 180s >> /root/heartproblem
				break
			fi
	 	fi
done

echo /pace/etcddel.py $leaderip sync/leader/Add_ --prefix

stamp=`date +%s%N`
/pace/etcddel.py $leaderip sync/leader/Add_ --prefix
echo /pace/etcdput.py $leaderip sync/leader/Add_${myhost}_$myhostip/request leader_$stamp
/pace/etcdput.py $leaderip sync/leader/Add_${myhost}_$myhostip/request leader_$stamp
echo /pace/etcdput.py $leaderip sync/leader/Add_${myhost}_$myhostip/request/$myhost leader_$stamp
/pace/etcdput.py $leaderip sync/leader/Add_${myhost}_$myhostip/request/$myhost leader_$stamp
echo finihsedfirst
templhttp='/TopStor/httpd_template.conf'
shttpdf='/TopStordata/httpd.conf'
rm -rf $shttpdf 
cp $templhttp $shttpdf
sed -i "s/MYCLUSTERH/$leaderip/g" $shttpdf
sed -i "s/MYCLUSTER/$leaderip/g" $shttpdf
echo running httpd fowrarder as I am not primary
docker run --rm --name httpd --hostname shttpd --net ${DOCKER_NET:-bridge0} -v /etc/localtime:/etc/localtime:ro -v /root/gitrepo/resolv.conf:/etc/resolv.conf -p $myclusterip:19999:19999 -p $myclusterip:80:80 -p $myclusterip:443:443 -v $shttpdf:/usr/local/apache2/conf/httpd.conf -v /root/topstorwebetc:/usr/local/apache2/topstorwebetc -v /topstorweb:/usr/local/apache2/htdocs/ -itd moataznegm/quickstor:git
docker run -itd --rm --name flask --hostname apisrv -v /etc/localtime:/etc/localtime:ro -v /pace/:/pace -v /pacedata/:/pacedata/ -v /root/gitrepo/resolv.conf:/etc/resolv.conf --net ${DOCKER_NET:-bridge0} -p $myclusterip:5001:5001 -v /TopStor/:/TopStor -v /TopStordata/:/TopStordata moataznegm/quickstor:flask3
# The API: fapilooper starts fapi.py in the flask container.  docker_setup.sh starts it on every node, but make sure
# it runs here too (a node set up without it would serve no API after the take over); detached, so heartbeat.py's
# wait on leaderlost.sh is not held by it, and only once.
pgrep -f /pace/fapilooper.sh >/dev/null || setsid nohup /pace/fapilooper.sh >/dev/null 2>&1 </dev/null &
# No monitoring container here.  What a client needs first after a take over is the cluster ip, etcd, the UI and the
# API, in that order.  grafana used to be recreated in this script (before httpd and flask, which cost them seconds; and
# with /ToStor/promgrafhosts, a path that does not exist).  It was thrown away again a moment later: leaderlost.sh runs
# promserver.sh once the API answers, and that script removes and recreates prometheus and grafana properly.
#/TopStor/topstorrecvreply.py $myhostip & disown
#/pace/iscsiwatchdog.sh $myhostip $myhost >/dev/null 2>/dev/null & disown 
#/pace/syncrequestlooper.sh $leaderip $myhost & disown
#/pace/fapilooper.sh & disown
#/pace/zfsping.py $leaderip $myhost & disown
#/pace/rebootmeplslooper.sh $leaderip $myhost & disown 
echo hhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhh
isbybyleader=`/TopStor/etcdget.py $leaderip bybyleader --prefix`
echo 'hihi'$isbybyleader | grep 'dhcp'
if [ $? -eq 0 ];
then
	oldleader=`echo $isbybyleader | awk -F '/' '{print $1}'`
	userreq=`echo $isbybyleader | awk -F '/' '{print $2}'`
	/TopStor/Evacuate.py $leaderip $myhost $oldleader $userreq
fi
