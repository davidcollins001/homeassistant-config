
# DIR=/etc/openhab/system
DIR=/home/ghost/homeassistant/conf/system

for f in `ls ${DIR}/*.service ${DIR}/*.timer`; do
    echo "$f ->  /usr/lib/systemd/system/`basename $f`"
    rm /usr/lib/systemd/system/`basename $f`
    ln -s ${DIR}/`basename $f` /usr/lib/systemd/system
    # echo "systemctl needs to be run: systemctl daemon-reload"
    # echo "systemctl needs to be run: systemctl enable `basename $f`"
    # echo "systemctl needs to be run: systemctl start `basename $f`"
    systemctl daemon-reload
    systemctl enable `basename $f`
    systemctl start `basename $f`
done
