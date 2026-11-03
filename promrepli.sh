#!/bin/sh
cd /TopStor/
leaderip=`echo $@ | awk '{print $1}'`
myhostip=`echo $@ | awk '{print $2}'`
echo $@  > /root/promserver
declare -a actives=(`/TopStor/etcdget.py $leaderip Active --prefix | awk '{print $2}' | awk -F"'" '{print $2}'`)
declare -a ports=(`echo -e ":9100\n:9101\n:19916"`)
rm -rf /prom/prom.yml
cp /TopStor/prom.yml  /prom/promyaml
counter=1
for port in "${ports[@]}"; do
	portstr=''
	for line in "${actives[@]}"; do
  	# Process each line in the loop
		portstr=${portstr}${line}${port}','
	done
	echo portstr="$portstr"
  	sed -i "s/PORTSTR${counter}/$portstr/g" /prom/promyaml
	counter=$((counter+1))
done
cat /prom/promyaml >> /prom/prom.yml
rm -rf /prom/promyaml
docker rm -f promserver
docker rm -f promgraf
	docker run -d -p $myhostip:9090:9090 -v /prom/prom.yml:/etc/prometheus/prometheus.yml -v /prom/:/prometheus -v /etc/passwd:/etc/passwd -v /etc/group:/etc/group --name promserver prom/prometheus
 	rm -rf /promgraf/hosts
 	cp /TopStor/promgrafhosts /promgraf/hosts
 	sed -i "s/MYCLUSTER/$myhostip/g" /promgraf/hosts 
 	docker run -d -p $myhostip:4000:3000 -e GF_SERVER_PROTOCOL=https -e GF_SERVER_CERT_FILE=/etc/grafana/TopStor.crt -e GF_SERVER_CERT_KEY=/etc/grafana/TopStor.key -v /promgraf/grafana.ini:/etc/grafana/grafana.ini -v /promgraf:/var/lib/grafana -v /promgraf/hosts:/etc/hosts -v /TopStor/grafana/TopStor.crt:/etc/grafana/TopStor.crt -v /TopStor/grafana/TopStor.key:/etc/grafana/TopStor.key --name promgraf grafana/grafana
