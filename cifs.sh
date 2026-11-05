#!/bin/sh
cd /TopStor
export ETCDCTL_API=3
echo $@ > /root/cifsshtmp
# usage: cifs.sh <resname> <mounts> <ipaddr> <prefix> <vtype> [domain domainsrvn domainsrvi domadmin adminpass]
# Positional arguments (cifs.py passes a list).  The old "echo $@ | awk" parsing let an EMPTY mounts argument shift every
# later field (ipaddr '24', prefix 'CIFS', image '10.11.11.160'): the running share was removed and the cluster interface
# was re-activated on every try (QC test 22 step 18, QSD5.315).  Bad arguments: exit 2 before anything is touched.
badargs() { echo "cifs.sh: bad arguments ($# given): $1 |$2| $3 $4 $5"; echo "cifs.sh: bad arguments ($# given)" >&2; exit 2; }
[ $# -ge 5 ] || badargs "$@"
resname=$1
mounts=`echo "$2" | sed 's/\-v/ \-v /g'`
ipaddr=$3
ipsubnet=$4
vtype=$5
[ -n "$resname" ] && [ -n "$2" ] && [ -n "$vtype" ] || badargs "$@"
echo "$ipaddr" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}$' || badargs "$@"
echo "$ipsubnet" | grep -Eq '^[0-9]{1,2}$' || badargs "$@"
[ "$ipsubnet" -ge 1 ] && [ "$ipsubnet" -le 32 ] || badargs "$@"
echo "$vtype" | grep -q '_'
if [ $? -eq 0 ];
then
	# domain share: domain, domain server name, domain server ip, admin, admin password
	[ $# -ge 10 ] && [ -n "$6" ] && [ -n "$8" ] || badargs "$@"
fi
echo params $@ > /root/cifstmp
echo name $resname >> /root/cifstmp
echo mounts $mounts >> /root/cifstmp
echo ip and subnet $ipaddr $ipsubnet >> /root/cifstmp
docker rm -f $resname
# The share address goes on cmynode only when it is really missing: "nmcli conn up cmynode" re-activates the whole cluster
# bond (slaves released and enslaved again), so it was a bond bounce on every share start, also when the address was there.
cmydev=`nmcli -g connection.interface-name conn show cmynode 2>/dev/null`
if [ -n "$cmydev" ];
then
	ip -4 -o addr show dev $cmydev | grep -q " inet $ipaddr/"
else
	ip -4 -o addr show | grep -q " inet $ipaddr/"
fi
ipmissing=$?
needup=0
if [ $ipmissing -ne 0 ];
then
	nmcli conn mod cmynode -ipv4.addresses ${ipaddr}/$ipsubnet
	nmcli conn mod cmynode +ipv4.addresses ${ipaddr}/$ipsubnet
	if [ $? -eq 0 ];
	then
		needup=1
	else
		echo "cifs.sh: nmcli could not add ${ipaddr}/$ipsubnet to cmynode, cmynode not re-activated"
	fi
else
	echo "cifs.sh: ${ipaddr} is already on ${cmydev:-the node}, cmynode not re-activated"
	# keep it in the profile too (a later "conn up" elsewhere must not drop it); a profile change alone bounces nothing
	nmcli -g ipv4.addresses conn show cmynode 2>/dev/null | grep -q "${ipaddr}/" || nmcli conn mod cmynode +ipv4.addresses ${ipaddr}/$ipsubnet
fi
echo $vtype | grep '_'
if [ $? -ne 0 ] && [ $needup -eq 1 ];
then
	nmcli conn up cmynode
	needup=0
fi
echo $vtype | grep '_'
if [ $? -ne 0 ];
then
		docker run -d $mounts --rm --privileged \
  		-e "HOSTIP=$ipaddr"  \
  		-p $ipaddr:135:135 \
  		-p $ipaddr:137:137/udp \
  		-p $ipaddr:138:138/udp \
  		-p $ipaddr:139:139 \
  		-p $ipaddr:445:445 \
  		-v /TopStordata/smb.${ipaddr}:/config/smb.conf:rw \
  		-v /TopStor/smb.conf:/etc/samba/smb.conf:rw \
  		-v /etc/:/hostetc/   \
  		-v /var/lib/samba/private:/var/lib/samba/private:rw \
  		--name $resname moataznegm/quickstor:smb
  		sleep 1 
  		docker exec $resname sh /hostetc/VolumeCIFSupdate.sh
else
	domain=$6
	domainsrvn=$7
	domainsrvi=$8
	domadmin=$9
	adminpass=${10}
 	wrkgrp=`echo $domain | awk -F'.' '{print $1}'`  
	membername=${wrkgrp}-$RANDOM
	cp /TopStor/smbmember.conf /etc/smbmember.conf_$membername
	sed -i "s/WRKGRP/$wrkgrp/g" /etc/smbmember.conf_$membername
	sed -i "s/DOMAINIP/$domainsrvi/g" /etc/smbmember.conf_$membername
	sed -i "s/DOMAIN/${domain^^}/g" /etc/smbmember.conf_$membername
  	cat /TopStordata/smb.${ipaddr} >> /etc/smbmember.conf_$membername
	echo nameserver $domainsrvi > /TopStordata/resolv_$wrkgrp
	#cp /TopStordata/smbmember.conf_$membername /etc/smbmember.conf_$membername
 	echo -e 'notyet=1 \nwhile [ $notyet -eq 1 ];\ndo\nsleep 3' > /etc/smb${membername}.sh
 	echo -e 'cat /etc/samba/smb.conf | grep' "'\[public\]'" >> /etc/smb${membername}.sh
 	echo -e 'if [ $? -eq 0 ];\nthen' >> /etc/smb${membername}.sh
 	echo -e ' cat /etc/samba/smb.conf | grep' "'\[private\]'" >> /etc/smb${membername}.sh
 	echo -e ' if [ $? -eq 0 ];\nthen' >> /etc/smb${membername}.sh
 	echo -e '  cat /etc/samba/smb.conf | grep' "'\[home\]'" >> /etc/smb${membername}.sh
 	echo -e '  if [ $? -eq 0 ];\nthen\nnotyet=0\nsleep 10\nfi\nfi\nfi\ndone' >> /etc/smb${membername}.sh
	echo -e "cat /hostetc/smbmember.conf_$membername > /etc/samba/smb.conf" >> /etc/smb${membername}.sh
 	echo  "service samba --full-restart"  >> /etc/smb${membername}.sh
 	chmod +w /etc/smb${membername}.sh
	myhost=`hostname`	
    	myclusterip=`docker exec etcdclient /TopStor/etcdgetlocal.py leaderip`
	echo myhost=$myhost myclusterip=$myclusterip
	mydns=`/TopStor/etcdget.py $myclusterip dnsname/$myhost`
	echo ll$mydns | grep '\.' 
	if [ $? -eq 0 ];
	then
		echo iamhere
		wantdns=$domainsrvi,$mydns
	else
		echo no dns
		wantdns=$domainsrvi
	fi
	# change the dns (and re-activate cmynode) only when it differs or the share address was just added: every
	# "conn up cmynode" bounces the cluster bond
	curdns=`nmcli -g ipv4.dns conn show cmynode 2>/dev/null | tr -d ' '`
	if [ "$curdns" != "$wantdns" ];
	then
		nmcli conn modify cmynode ipv4.dns $wantdns && needup=1
		echo nmcli conn modify cmynode ipv4.dns $wantdns
	fi
	if [ $needup -eq 1 ];
	then
		nmcli conn up cmynode
	else
		echo "cifs.sh: address and dns already set, cmynode not re-activated"
	fi
 	sync
 	adminpass=`echo $adminpass | sed 's/\@\@sep/\//g'`
 	adminpass=`/TopStor/decthis.sh $domain $adminpass | awk -F'_result' '{print $2}' `
 	docker run -d  $mounts --privileged --rm --add-host "${membername}.$domain ${membername}":$ipaddr  \
  		--hostname ${membername} \
  		-v /etc/localtime:/etc/localtime:ro \
  		-v /etc/:/hostetc/   \
  		-v /TopStordata/smb.${ipaddr}:/etc/smb.conf:rw \
		-v /TopStordata/resolv_$wrkgrp:/etc/resolv.conf \
  		-e DOMAIN_NAME=$domain \
  		-e ADMIN_SERVER=$domainsrvi \
  		-e WORKGROUP=$wrkgrp  \
  		-e AD_USERNAME=$domadmin \
  		-e AD_PASSWORD=$adminpass \
  		-p $ipaddr:137:137/udp \
  		-p $ipaddr:138:138/udp \
  		-p $ipaddr:139:139/tcp \
  		-p $ipaddr:445:445/tcp \
  		--name $resname moataznegm/quickstor:membersmb 
	#cat /TopStordata/resolv.conf > /etc/resolv.conf
	echo /TopStor/dockmember.sh $resname sh /hostetc/smb${membername}.sh 
	/TopStor/dockmember.sh $resname sh /hostetc/smb${membername}.sh

fi
