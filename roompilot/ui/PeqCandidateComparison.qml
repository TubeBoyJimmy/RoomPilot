import QtQuick
import QtQuick.Layouts

RPCard {
    id: panel
    required property var app
    property var comparison: ({})
    property var preview: ({})
    property bool isPreview: false
    property bool historical:!!comparison.historical
    function selectedCandidate() {
        var rows=comparison.candidates || [];
        for(var i=0;i<rows.length;i++) if(rows[i].key === comparison.selected_key) return rows[i];
        return {};
    }
    function inspectNotes() {
        var lines=["最多三個候選使用相同目標、頻段與設備限制，比較不同的取捨；相同參數會合併。", "容許量：" + app.num(comparison.low_cut_limit_db,2) + " dB RMS", "判斷各位置 RMS 的最大值；不是每個頻點的最大衰減，也不是聲學標準。", "", "殘留波峰使用整個校正頻段；額外減益只統計原本低於目標的頻點。兩者分母不同，不可相加。", "預選僅為較少額外減益的檢視起點，不代表最佳或已獲實測驗證。"];
        lines=lines.concat([""],comparison.notes || []);
        var rows=comparison.candidates || [];
        for(var i=0;i<rows.length;i++) {
            var c=rows[i];
            lines.push("",c.title || c.key,c.within_limit ? "所有位置均在容許量內" : "至少一個位置超過容許量");
            if(c.within_limit && c.pareto === false) lines.push("本輪另有合格候選在兩項主要指標均不差，且至少一項更好。");
            if(!c.within_limit) lines.push("未納入合格候選的取捨比較。");
            for(var j=0;j<(c.limit_violations || []).length;j++) { var v=c.limit_violations[j]; lines.push((v.id || v.channel + " · " + v.position) + "：" + app.num(v.value_db,2) + " dB RMS，門檻 " + app.num(v.limit_db,2)); }
            lines=lines.concat(c.notes || []);
        }
        notesDialog.heading="這次候選如何比較";
        notesDialog.subheading="數值是濾波器預測，實際結果需補錄";
        notesDialog.bodyText=lines.join("\n");notesDialog.open();
    }
    implicitHeight: content.implicitHeight + 36
    ColumnLayout {
        id: content
        anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:18
        spacing:12
        RowLayout {
            Layout.fillWidth:true
            SectionTitle { title:panel.historical ? "當時的候選比較" : "比較候選，選定後再儲存"; subtitle:panel.historical ? "保存選擇當時的取捨與容許門檻" : "相同目標與限制 · 預覽不會新增 PEQ 版本"; Layout.fillWidth:true }
            RPButton { objectName:"inspectCandidateComparison"; text:"比較說明"; compact:true; variant:"ghost"; onClicked:panel.inspectNotes() }
        }
        Text { text:"額外減益容許量 " + app.num(comparison.low_cut_limit_db,2) + " dB RMS · 各位置最大值"; color:"#89a7bd"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap }
        Repeater {
            model:comparison.candidates || []
            delegate:Rectangle {
                id: row
                required property var modelData
                property bool chosen:(panel.isPreview || panel.historical) && modelData.key === panel.comparison.selected_key
                Layout.fillWidth:true
                implicitHeight:rowContent.implicitHeight + 22
                radius:7; color:chosen ? "#19333d" : "#102130"
                border.color:chosen ? "#508c81" : "#24394b"
                ColumnLayout {
                    id:rowContent
                    anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:11
                    spacing:9
                    RowLayout {
                        Layout.fillWidth:true; spacing:8
                        Text { text:modelData.title || modelData.key; color:"#d1e3ed"; font.pixelSize:13; font.weight:Font.DemiBold; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                        Text { text:modelData.within_limit ? "容許量內" : "超過容許量"; color:modelData.within_limit ? "#83cbb7" : "#dfc28c"; font.pixelSize:10 }
                        RPButton { objectName:"previewCandidate_" + modelData.key; visible:!panel.historical; text:row.chosen ? "預覽中" : "預覽"; variant:row.chosen ? "primary" : "secondary"; compact:true; enabled:!panel.app.s.busy; onClicked:bridge.selectPeqCandidate(modelData.key) }
                        Text { visible:panel.historical && row.chosen; text:"當時選定"; color:"#83cbb7"; font.pixelSize:11 }
                    }
                    RowLayout {
                        Layout.fillWidth:true; spacing:14
                        ColumnLayout {
                            Layout.fillWidth:true; Layout.preferredWidth:1; spacing:3
                            Text { text:"殘留波峰"; color:"#8aa6ba"; font.pixelSize:10 }
                            Text { text:panel.app.num((modelData.metrics || {}).residual_peak_rms_db,2) + " dB RMS"; color:"#d4e5ee"; font.pixelSize:14; font.family:"Segoe UI" }
                        }
                        ColumnLayout {
                            Layout.fillWidth:true; Layout.preferredWidth:1; spacing:3
                            Text { text:"原低點額外減益 · 位置最大"; color:"#8aa6ba"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                            Text { text:panel.app.num((modelData.metrics || {}).worst_below_target_cut_rms_db,2) + " dB RMS"; color:modelData.within_limit ? "#d4e5ee" : "#dfc28c"; font.pixelSize:14; font.family:"Segoe UI" }
                        }
                    }
                    Text { visible:!!modelData.is_recommended; text:"較少額外減益的預覽起點；不代表最佳方案"; color:"#759e99"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                    Text { visible:modelData.active_band_count === 0; text:"無校正參照：本輪沒有產生有效修正，不列為 PEQ 建議。"; color:"#b7b391"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                    Text { visible:!!((modelData.result || {}).settings || {}).allow_boost; text:"沿用進階增益限制；此候選可供比較，不列為只減益的預選建議。"; color:"#b7b391"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                }
            }
        }
        Text { visible:!!(comparison.reference_metrics || {}).summary; text:"不套用 PEQ 參照：殘留波峰 " + app.num((((comparison.reference_metrics || {}).summary || {}).after || (comparison.reference_metrics || {}).summary || {}).residual_peak_rms_db,2) + " dB RMS；額外減益 0。"; color:"#819bad"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
        RowLayout {
            visible:!panel.historical
            Layout.fillWidth:true; spacing:10
            RPButton { objectName:"savePeqCandidate"; text:"儲存選定候選"; variant:"primary"; Layout.fillWidth:true; enabled:panel.isPreview && !!panel.selectedCandidate().within_limit && !panel.app.s.busy; onClicked:bridge.savePeqCandidate(panel.comparison.selected_key) }
            RPButton { objectName:"discardPeqCandidates"; text:"關閉比較"; enabled:!panel.app.s.busy; onClicked:bridge.discardPeqCandidates() }
        }
        Text { visible:!panel.historical && panel.isPreview && !panel.selectedCandidate().within_limit; text:"目前候選超過容許量。若要採用，請調整左側容許量後重新比較。"; color:"#dfc28c"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap }
        Text { text:panel.historical ? "此為歷史紀錄；重新產生候選不會改寫當時的選擇。" : panel.isPreview ? "尚未儲存的比較只保留在本次工作階段。" : "目前查看已儲存方案；點選「預覽」可回到本次比較。"; color:"#6f899f"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
    }
    InspectDialog { id:notesDialog; objectName:"candidateComparisonDialog" }
}
