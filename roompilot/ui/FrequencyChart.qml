import QtQuick
import QtQuick.Layouts

Item {
    id: chart
    property var curves: []
    property real frequencyMin: 20
    property real frequencyMax: 20000
    property bool lowOnly: false
    property bool responseMode: false
    property bool showBandCurves: false
    property string channelFilter: "Both"
    readonly property var visibleCurves: curves.filter(function(c) {
        var matches = chart.channelFilter === "Both" || !c.channel || c.channel === "Shared" || c.channel === chart.channelFilter;
        return matches && (c.kind !== "per_band" || chart.showBandCurves);
    })
    readonly property var legendCurves: visibleCurves.filter(function(c) { return c.kind !== "per_band" && c.kind !== "zero"; })
    property string emptyText: "匯入量測後，頻率響應將顯示在這裡"
    implicitHeight: 330
    function colorFor(c, index) {
        if (c.kind === "zero") return "#8294a6";
        if (c.kind === "peq_total") return c.channel === "R" ? "#a0b5ff" : "#83dbc5";
        if (c.kind === "per_band") return c.channel === "R" ? "#7988bd" : "#508d83";
        if (c.kind === "target") return "#e4c487";
        if (c.kind === "predicted") return c.channel === "R" ? "#8dcefb" : "#83dbc5";
        if (c.kind === "verification" || c.kind === "verified") return c.channel === "R" ? "#b49be5" : "#83dbc5";
        if (c.kind === "verified_aligned") return c.channel === "R" ? "#b4c2fb" : "#c5ecd4";
        return c.channel === "R" ? "#879dce" : "#81949d";
    }
    onVisibleCurvesChanged: plot.requestPaint()
    onLowOnlyChanged: plot.requestPaint()
    onFrequencyMinChanged: plot.requestPaint()
    onFrequencyMaxChanged: plot.requestPaint()
    onResponseModeChanged: plot.requestPaint()
    RowLayout {
        id: toolbar
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        spacing: 5
        Repeater {
            model: [{label:"左右",value:"Both"},{label:"左 L",value:"L"},{label:"右 R",value:"R"}]
            delegate: RPButton {
                required property var modelData
                objectName: chart.objectName + "Channel" + modelData.value
                text: modelData.label
                compact: true
                implicitWidth: 62
                variant: chart.channelFilter === modelData.value ? "primary" : "ghost"
                onClicked: chart.channelFilter = modelData.value
            }
        }
        Item { Layout.fillWidth: true }
        RPCheckBox {
            objectName: chart.objectName + "ShowBands"
            visible: chart.responseMode
            text: "各 Band"
            checked: chart.showBandCurves
            onClicked: chart.showBandCurves = checked
        }
    }
    Canvas {
        id: plot
        objectName: chart.objectName + "Canvas"
        anchors.fill: parent
        anchors.topMargin: toolbar.height + 9
        anchors.bottomMargin: Math.max(23, legend.height + 9)
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            var ctx = getContext("2d");
            ctx.reset();
            var left = 49, right = 17, top = 17, bottom = 34;
            var pw = width - left - right, ph = height - top - bottom;
            if (pw <= 0 || ph <= 0) return;
            var fmin = Math.max(1, chart.frequencyMin), fmax = chart.lowOnly ? Math.min(500, chart.frequencyMax) : chart.frequencyMax;
            if (fmax <= fmin) fmax = fmin * 10;
            var low = chart.responseMode ? 0 : Infinity, high = chart.responseMode ? 0 : -Infinity, valid = false;
            for (var c = 0; c < chart.visibleCurves.length; c++) {
                var curve = chart.visibleCurves[c], ff = curve.frequency || [], vv = curve.spl || [];
                for (var j = 0; j < Math.min(ff.length, vv.length); j++) {
                    if (ff[j] >= fmin && ff[j] <= fmax && isFinite(vv[j])) {
                        low = Math.min(low, vv[j]); high = Math.max(high, vv[j]); valid = true;
                    }
                }
            }
            var yStep;
            if (chart.responseMode) {
                if (!valid) { low = -6; high = 0; }
                var range = Math.max(2, high - low);
                yStep = range > 15 ? 5 : range > 7 ? 2 : 1;
                low = Math.floor((low - 0.35) / yStep) * yStep;
                high = Math.ceil((high + 0.35) / yStep) * yStep;
                if (high - low < 2) { low = -1; high = 1; }
            } else {
                if (!valid) { low = 40; high = 90; }
                low = Math.floor((low - 3) / 5) * 5;
                high = Math.ceil((high + 3) / 5) * 5;
                if (high - low < 20) { var mid = (high + low) / 2; low = mid - 10; high = mid + 10; }
                yStep = Math.max(5, Math.ceil((high - low) / 8 / 5) * 5);
            }
            var span = Math.log(fmax / fmin);
            function px(f) { return left + Math.log(f / fmin) / span * pw; }
            function py(v) { return top + (high - v) / (high - low) * ph; }
            ctx.font = "11px 'Segoe UI'";
            ctx.fillStyle = "#70869c";
            ctx.strokeStyle = "#253449";
            ctx.lineWidth = 1;
            for (var db = Math.ceil(low / yStep) * yStep; db <= high; db += yStep) {
                var y = py(db);
                ctx.strokeStyle = chart.responseMode && db === 0 ? "#637587" : "#253449";
                ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(left + pw, y); ctx.stroke();
                ctx.textAlign = "right"; ctx.fillText(String(db), left - 10, y + 4);
            }
            ctx.strokeStyle = "#253449";
            var ticks = [20, 30, 50, 80, 100, 200, 300, 500, 1000, 2000, 5000, 10000, 20000];
            var lastLabel = -100;
            for (var k = 0; k < ticks.length; k++) {
                var f = ticks[k]; if (f < fmin || f > fmax) continue;
                var x = px(f);
                ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + ph); ctx.stroke();
                if (x - lastLabel > 37) { ctx.textAlign = "center"; ctx.fillText(f >= 1000 ? (f / 1000) + "k" : String(f), x, top + ph + 20); lastLabel = x; }
            }
            ctx.textAlign = "left"; ctx.fillStyle = "#8fa3b5"; ctx.fillText(chart.responseMode ? "Gain dB" : "dB", 2, 12);
            ctx.textAlign = "right"; ctx.fillText("Hz", width - 2, height - 2);
            ctx.save(); ctx.beginPath(); ctx.rect(left, top, pw, ph); ctx.clip();
            var drawCurves = chart.visibleCurves.slice().sort(function(a,b) { return (a.kind === "per_band" ? 0 : 1) - (b.kind === "per_band" ? 0 : 1); });
            for (var n = 0; n < drawCurves.length; n++) {
                var item = drawCurves[n], freq = item.frequency || [], vals = item.spl || [];
                ctx.strokeStyle = chart.colorFor(item, n);
                ctx.lineWidth = item.kind === "peq_total" ? 2.6 : item.kind === "per_band" ? 1.1 : item.kind === "predicted" || item.kind === "verification" || item.kind === "verified" ? 2.1 : 1.4;
                ctx.globalAlpha = item.kind === "per_band" ? 0.7 : item.kind === "baseline" ? 0.85 : 1;
                ctx.setLineDash(item.kind === "target" || item.kind === "zero" ? [5, 5] : item.kind === "baseline" ? [2, 2] : item.kind === "verified_aligned" ? [7, 3] : []);
                ctx.beginPath(); var started = false;
                var stride = Math.max(1, Math.floor(freq.length / Math.max(600, pw * 3)));
                for (var t = 0; t < Math.min(freq.length, vals.length); t += stride) {
                    if (freq[t] < fmin || freq[t] > fmax || !isFinite(vals[t])) continue;
                    if (!started) { ctx.moveTo(px(freq[t]), py(vals[t])); started = true; }
                    else ctx.lineTo(px(freq[t]), py(vals[t]));
                }
                ctx.stroke();
            }
            ctx.restore();
        }
    }
    Text {
        anchors.centerIn: plot
        visible: chart.visibleCurves.length === 0
        text: chart.curves.length ? "此檢視沒有對應聲道資料" : chart.emptyText
        color: "#8fa3b5"
        font.pixelSize: 13
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        width: parent.width - 120
    }
    Flow {
        id: legend
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.leftMargin: 48
        anchors.right: parent.right
        spacing: 15
        Repeater {
            model: chart.legendCurves
            delegate: Row {
                required property var modelData
                required property int index
                spacing: 6
                Rectangle { width: 15; height: 2; anchors.verticalCenter: parent.verticalCenter; color: chart.colorFor(modelData, index) }
                Text { text: modelData.name || modelData.channel || "量測"; color: "#9bafc1"; font.pixelSize: 10; elide: Text.ElideRight; width: Math.min(implicitWidth, 180) }
            }
        }
        Text { visible: chart.responseMode && chart.showBandCurves; text: "細線：各 Band"; color: "#7897a9"; font.pixelSize: 10 }
    }
}
