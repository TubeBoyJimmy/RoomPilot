import QtQuick
import QtQuick.Layouts

RPCard {
    id: panel
    required property var app
    property var comparison: ({})
    property var preview: ({})
    property var savedVariants: []
    property bool isPreview: false
    property bool savedGroup: !isPreview && savedVariants.length > 0
    property bool historical: !!comparison.historical && !savedGroup
    property var rows: savedGroup ? savedRows() : (comparison.candidates || [])
    property var chosen: selectedCandidate()
    function savedRows() {
        var result=[], candidates=comparison.candidates || [];
        for(var i=0;i<savedVariants.length;i++) {
            var member=savedVariants[i], row={};
            for(var j=0;j<candidates.length;j++) if(candidates[j].key === (member.key || member.variant_key)) {row=Object.assign({},candidates[j]);break;}
            row.id=member.id;row.key=member.key || member.variant_key;
            row.title=member.title || member.variant_title || row.title || member.name;
            row.metrics=member.metrics || row.metrics || {};
            result.push(row);
        }
        return result;
    }
    function selectedCandidate() {
        for(var i=0;i<rows.length;i++) if(savedGroup ? rows[i].id === preview.id : rows[i].key === comparison.selected_key) return rows[i];
        return {};
    }
    function purpose(candidate) {
        if(candidate.purpose) return candidate.purpose;
        var mode=(((candidate.result || {}).settings || {}).objective_mode || (preview.settings || {}).objective_mode);
        if(mode === "shape") return "在此策略的額外減益容許量內，精修波峰與具多位置支持的寬低處。";
        var title=candidate.title || "";
        if(title === "保護低處") return "保留較多低處餘裕，在較小額外減益容許量內削峰。";
        if(title === "平衡削峰") return "使用中等額外減益容許量，兼顧削峰與鄰近低處。";
        if(title === "充分削峰") return "在你設定的完整容許量內，繼續降低殘留波峰。";
        return "同一目標與設備限制下的另一種取捨。";
    }
    function inspectNotes() {
        var currentModel=comparison.schema_version >= 2 || comparison.budget_fractions !== undefined;
        var lines=[currentModel ? "三種策略使用相同目標與設備限制，分別在較小、中等及完整額外減益容許量內搜尋。延伸原案時，預算先包含原案已有的減益。三個標籤會一起保存在同一 PEQ 版本；有時不同預算仍得到相同參數。" : "這是先前模型產生的候選比較，保留當時的策略與門檻。", "你設定的總容許量：" + app.num(comparison.low_cut_limit_db,2) + " dB RMS", "判斷各位置 RMS 的最大值；不是每個頻點的最大衰減，也不是聲學標準。", "", "殘留波峰使用整個校正頻段；額外減益只統計原本低於目標的頻點。兩者分母不同，不可相加。", "預選是比較的起點，不代表最佳聽感或已獲實測驗證。"];
        lines=lines.concat([""],comparison.notes || []);
        for(var i=0;i<rows.length;i++) {
            var c=rows[i];
            lines.push("",c.title || c.key,purpose(c),"此候選容許量：" + app.num(c.low_cut_limit_db !== undefined ? c.low_cut_limit_db : comparison.low_cut_limit_db,2) + " dB RMS",c.within_limit ? "所有位置均在此候選容許量內" : "至少一個位置超過容許量");
            if(c.within_limit && c.pareto === false) lines.push("本輪另有合格候選在兩項主要指標均不差，且至少一項更好。");
            if(!c.within_limit) lines.push("未納入合格候選的取捨比較。");
            for(var j=0;j<(c.limit_violations || []).length;j++) { var v=c.limit_violations[j]; lines.push((v.id || v.channel + " · " + v.position) + "：" + app.num(v.value_db,2) + " dB RMS，門檻 " + app.num(v.limit_db,2)); }
            lines=lines.concat(c.notes || []);
        }
        notesDialog.heading="這次候選如何比較";
        notesDialog.subheading="數值是濾波器預測，實際結果需補錄";
        notesDialog.bodyText=lines.join("\n");notesDialog.open();
    }
    implicitHeight: content.implicitHeight + 32
    ColumnLayout {
        id: content
        anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:16
        spacing:10
        RowLayout {
            Layout.fillWidth:true; spacing:8
            Text { text:panel.historical ? "當時的候選比較" : "此版本的 PEQ 策略"; color:"#dce8f2"; font.pixelSize:16; font.weight:Font.DemiBold; Layout.fillWidth:true }
            RPButton { objectName:"inspectCandidateComparison"; text:"比較說明"; compact:true; variant:"ghost"; onClicked:panel.inspectNotes() }
        }
        RowLayout {
            Layout.fillWidth:true; spacing:6
            Repeater {
                model:panel.rows
                delegate:RPButton {
                    required property var modelData
                    property bool chosen:panel.savedGroup ? modelData.id === panel.preview.id : (panel.isPreview || panel.historical) && modelData.key === panel.comparison.selected_key
                    objectName:"previewCandidate_" + modelData.key
                    Layout.fillWidth:true; Layout.preferredWidth:1
                    compact:true; leftPadding:6; rightPadding:6
                    text:modelData.title || modelData.key
                    variant:chosen ? "primary" : "secondary"
                    enabled:!panel.historical && !panel.app.s.busy
                    opacity:panel.historical ? (chosen ? 1 : 0.6) : 1
                    onClicked:panel.savedGroup ? bridge.selectPeq(modelData.id) : bridge.selectPeqCandidate(modelData.key)
                }
            }
        }
        Text { visible:!!panel.chosen.key; text:panel.purpose(panel.chosen); color:"#9cb8c8"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.35 }
        Text { visible:(panel.chosen.identical_to || []).length > 0; text:"此策略的參數與本版另一策略相同；不同容許量不一定需要不同濾波器。"; color:"#7894aa"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
        RowLayout {
            visible:!!panel.chosen.key
            Layout.fillWidth:true; spacing:12
            Text { text:"殘留波峰 " + panel.app.num((panel.chosen.metrics || {}).residual_peak_rms_db,2) + " dB RMS"; color:"#b8d4e1"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap }
            Text { text:"低處額外減益 " + panel.app.num((panel.chosen.metrics || {}).worst_below_target_cut_rms_db,2) + " / " + panel.app.num(panel.chosen.low_cut_limit_db !== undefined ? panel.chosen.low_cut_limit_db : comparison.low_cut_limit_db,2) + " dB RMS"; color:panel.chosen.within_limit ? "#8db9ac" : "#dfc28c"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap }
        }
        Text { visible:panel.chosen.active_band_count === 0; text:"本輪沒有產生有效修正；此結果作為不套用 EQ 的參照。"; color:"#c2b891"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
        Text { visible:panel.historical; text:"此為歷史比較，亮色標籤是當時保存的方案；下方曲線顯示該版本。"; color:"#7894aa"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
        Text { visible:panel.savedGroup; text:"切換標籤可查看同一版本內的完整參數；匯出與套用記錄會使用目前策略。"; color:"#7894aa"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
    }
    InspectDialog { id:notesDialog; objectName:"candidateComparisonDialog" }
}
