#!/system/bin/sh
set -u

PKGS="com.chinamworld.main com.sdu.didi.psnger com.tencent.tmgp.pubgmhd me.ele com.baidu.homework com.tencent.mm com.cainiao.wireless com.kugou.android com.achievo.vipshop com.tencent.KiHan com.chinamworld.bocmbci com.ss.android.article.video com.zhihu.android com.MobileTicket com.tencent.news cn.wps.moffice_eng com.alibaba.android.rimet com.baidu.searchbox com.kmxs.reader com.qidian.QDReader com.tencent.tmgp.sgame com.xs.fm com.kuaiduizuoye.scan com.phoenix.read com.icbc com.mfashiongallery.emag com.dz.hmjc com.netease.cloudmusic com.tencent.mtt com.sina.weibo com.tencent.jkchess com.cmri.universalapp com.sinovatech.unicom.ui com.moji.mjweather com.microsoft.emmx com.baidu.searchbox.lite com.shizhuang.duapp com.cat.readall com.xs.fm.lite com.baidu.BaiduMap "
LOG="/data/local/tmp/dex2oat_40apps_loop.log"
PID_FILE="/data/local/tmp/dex2oat_40apps_loop.pid"
INTERVAL="0.015"

echo "$$" > "$PID_FILE"
: > "$LOG"
echo "$(date +'%F %T') dex2oat loop started pid=$$ interval=$INTERVAL" >> "$LOG"

cycle=0
while :; do
  cycle=$((cycle + 1))
  for pkg in $PKGS; do
    echo "$(date +'%F %T') cycle=$cycle pkg=$pkg action=delete-dexopt" >> "$LOG"
    pm delete-dexopt "$pkg" >/dev/null 2>&1 || true
    echo "$(date +'%F %T') cycle=$cycle pkg=$pkg action=compile" >> "$LOG"
    pm compile --full -r cmdline -f -m speed-profile "$pkg" >/dev/null 2>&1 || true
    sleep "$INTERVAL"
  done
done
