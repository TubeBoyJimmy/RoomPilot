import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: panel
    required property var app
    property var result: ({})
    property var explanation:result.explanation || ({})
    property var before:(explanation.summary || {}).before || ({})
    property var after:(explanation.summary || {}).after || ({})
    property var protection:explanation.protection || ({})
    property var protectedSummary:protection.summary || ({})
    property bool v5Policy:(result.settings || {}).guard_policy === "v5"
    property bool shapeMode: ((explanation.evaluation_policy || {}).objective_mode || (result.settings || {}).objective_mode) === "shape"
    property bool showPositions:false
    property bool showProtectionDetails:false
    property bool showRmse:false
    function guardRows() {
        return [
            {title:"所有頻點新增低處",value:protectedSummary.worst_added_deficit_rms_db,unit:"dB RMS",note:"各原始量測 RMS 的最大值"},
            {title:"單一頻點新增低處",value:protectedSummary.worst_added_deficit_max_db,unit:"dB",note:"所有原始量測、頻點的最大值"},
            {title:"最深局部平均減益",value:protectedSummary.max_full_local_cut_db !== undefined ? protectedSummary.max_full_local_cut_db : protectedSummary.max_local_cut_db,unit:"dB",note:protectedSummary.max_full_local_cut_db !== undefined ? "完整 PEQ 曲線 · 1/3 octave 頻窗" : "量測頻段 · 1/3 octave 頻窗"},
            {title:"固定低頻平均減益",value:protectedSummary.max_bass_mean_cut_db,unit:"dB",note:"80–200 Hz · 各設定聲道最大值"}
        ];
    }
    function channelGuards() {
        var channels=protection.channels || {}, keys=Object.keys(channels), rows=[];
        for(var i=0;i<keys.length;i++) rows.push({channel:keys[i],diagnostic:channels[keys[i]]});
        return rows;
    }
    function metric(value, unit) { return value === undefined || value === null ? "資料不足" : app.num(value,2) + " " + unit; }
    function coverageWarnings() {
        var channels=channelGuards(), lines=[];
        for(var i=0;i<channels.length;i++) {
            var bass=channels[i].diagnostic.bass_mean_cut || {}, local=channels[i].diagnostic.local_cut || {};
            if(bass.coverage_complete !== true) lines.push(channels[i].channel + " 的 80–200 Hz 未完整涵蓋；顯示值僅代表可用部分，或尚無資料。");
            if(local.coverage_complete !== true) lines.push(channels[i].channel + " 的滑動頻窗涵蓋不完整或未記錄；請展開涵蓋範圍查看。");
        }
        return lines.join("\n");
    }
    function coverageText(value, fixed) {
        if(value.mean_db === null || value.actual_min_hz === null || value.actual_min_hz === undefined) return "資料不足，尚無可判讀的涵蓋範圍";
        return (value.coverage_complete === true ? "完整涵蓋" : value.coverage_complete === false ? "僅涵蓋部分範圍" : "涵蓋狀態未記錄") + " · " + app.num(value.actual_min_hz,1) + "–" + app.num(value.actual_max_hz,1) + " Hz" + (fixed && value.coverage_complete !== true ? "；此值不能代表完整 80–200 Hz" : "");
    }
    function outsideFrequency(value) {
        if(value.frequency_hz === undefined || value.frequency_hz === null) return "頻率未記錄";
        if(Math.abs(value.frequency_hz - value.requested_max_hz) < Math.max(.001,value.requested_max_hz * 1e-6)) return app.num(value.requested_max_hz,1) + " Hz 上界外側";
        if(Math.abs(value.frequency_hz - value.requested_min_hz) < Math.max(.001,value.requested_min_hz * 1e-6)) return app.num(value.requested_min_hz,1) + " Hz 下界外側";
        return app.num(value.frequency_hz,1) + " Hz";
    }
    spacing:10
    Text { objectName:"peqSavedPolicyLabel"; text:panel.v5Policy ? "此版本：v0.5 校正保護 · 工程診斷，非聽感評分" : "此版本：舊計算政策 · 保留當時結果，未宣告通過 v0.5 保護"; color:panel.v5Policy ? "#8ebfaf" : "#dfc28c"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
    Text { visible:!!explanation.post_hoc; text:"以下為此舊方案的事後分析；原始參數與歷史計算結果未改寫。"; color:"#9ab0c0"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
    RowLayout {
        visible:!!explanation.summary
        Layout.fillWidth:true; spacing:9
        Rectangle {
            Layout.fillWidth:true; Layout.preferredWidth:1; implicitHeight:peakContent.implicitHeight + 24; radius:7; color:"#102130"
            ColumnLayout {
                id:peakContent
                anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:12; spacing:5
                Text { text:panel.shapeMode ? "有支持頻點的修正誤差" : "殘留波峰"; color:"#94adbf"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                Text { text:app.num(panel.shapeMode ? panel.after.primary_rms_db : panel.after.residual_peak_rms_db,2) + " dB RMS"; color:"#d2e2ed"; font.pixelSize:18; font.family:"Segoe UI"; font.weight:Font.DemiBold }
                Text { text:"原始 " + app.num(panel.shapeMode ? panel.before.primary_rms_db : panel.before.residual_peak_rms_db,2) + " dB RMS"; color:"#7997ae"; font.pixelSize:10 }
            }
        }
        Rectangle {
            Layout.fillWidth:true; Layout.preferredWidth:1; implicitHeight:cutContent.implicitHeight + 24; radius:7; color:"#102130"
            ColumnLayout {
                id:cutContent
                anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:12; spacing:5
                Text { text:"原低於目標頻點的額外減益"; color:"#94adbf"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                Text { text:panel.metric(panel.v5Policy ? panel.protectedSummary.worst_below_target_cut_rms_db : panel.after.worst_below_target_cut_rms_db,"dB RMS"); color:"#d2e2ed"; font.pixelSize:18; font.family:"Segoe UI"; font.weight:Font.DemiBold }
                Text { text:panel.v5Policy ? "各原始量測 RMS 的最大值" : "各位置平均曲線 RMS 的最大值"; color:"#7997ae"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
            }
        }
    }
    GridLayout {
        objectName:"peqProtectionMetrics"
        visible:!!panel.protection.summary
        Layout.fillWidth:true; columns:2; columnSpacing:9; rowSpacing:9
        Repeater {
            model:panel.guardRows()
            delegate:Rectangle {
                required property var modelData
                Layout.fillWidth:true; Layout.preferredWidth:1; implicitHeight:guardContent.implicitHeight + 22; radius:7; color:"#102130"
                ColumnLayout {
                    id:guardContent
                    anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:11; spacing:5
                    Text { text:modelData.title; color:"#94adbf"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                    Text { text:panel.metric(modelData.value,modelData.unit); color:"#d2e2ed"; font.pixelSize:16; font.family:"Segoe UI"; font.weight:Font.DemiBold }
                    Text { text:modelData.note; color:"#7997ae"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                }
            }
        }
    }
    Text { objectName:"peqOutsideCutMetric"; visible:panel.protectedSummary.max_outside_cut_db !== undefined; text:"指定校正頻段外最大減益：" + panel.metric(panel.protectedSummary.max_outside_cut_db,"dB") + (panel.v5Policy ? " ／ 上限 " + panel.app.num((panel.result.settings || {}).outside_cut_limit_db,2) + " dB" : " · 事後診斷，原政策未施加此保護"); color:"#a6c4d1"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
    Text { visible:panel.protectedSummary.max_outside_cut_db !== undefined; text:"完整 PEQ 曲線另檢查指定校正頻段外的影響，避免濾波器堆在邊界。沒有 SPL 量測處僅計算濾波器減益，不代表已確認空間響應或聽感。"; color:"#7997ae"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
    Text { visible:!!panel.protection.summary; text:"新增低處包含原本高於目標、校正後才落到目標以下的頻點。局部與固定低頻減益檢查整組 PEQ 合成曲線，均不含前級衰減；這些數字不能用來判定聲道音量平衡或聽感優劣。"; color:"#7997ae"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
    Text { objectName:"peqProtectionCoverageWarning"; visible:!!panel.protection.summary && text.length > 0; text:panel.coverageWarnings(); color:"#dfc28c"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
    Text { visible:!!panel.protection.summary && panel.protection.enforced === false; text:"保護診斷供參考；此方案未宣告所有 v0.5 保護限制均已通過。"; color:"#dfc28c"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
    Text { visible:!!explanation.summary; text:panel.shapeMode ? "精修誤差計入殘留波峰與具多位置支持的寬低處；深窄凹洞不因此獲得增益。殘留波峰另為 " + app.num(panel.after.residual_peak_rms_db,2) + " dB RMS。原低處額外減益只取原先低於目標的頻點，與主目標不能相加。" : "殘留波峰使用整個校正頻段；原低處額外減益只取原先低於目標的頻點。分母不同，不能相加；最差 RMS 不是單一頻點的最大衰減。"; color:"#7997ae"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
    Text { visible:!explanation.summary; text:"此舊版本未保存這兩項可讀指標；可產生新候選重新分析。"; color:"#9ab0c0"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap }
    RowLayout {
        Layout.fillWidth:true; spacing:10
        RPButton { objectName:"togglePeqProtectionDetails"; visible:!!panel.protection.summary; text:panel.showProtectionDetails ? "收合保護細節" : "原始量測與涵蓋範圍"; compact:true; variant:"ghost"; onClicked:panel.showProtectionDetails=!panel.showProtectionDetails }
        RPButton { objectName:"togglePeqPositionMetrics"; visible:!!explanation.summary; text:panel.showPositions ? "收合各位置" : "展開各聲道／位置"; compact:true; variant:"ghost"; onClicked:panel.showPositions=!panel.showPositions }
        Item { Layout.fillWidth:true }
    }
    ColumnLayout {
        visible:panel.showProtectionDetails && !!panel.protection.summary
        Layout.fillWidth:true; spacing:9
        Repeater {
            model:panel.channelGuards()
            delegate:Rectangle {
                required property var modelData
                Layout.fillWidth:true; implicitHeight:guardDetailContent.implicitHeight + 22; radius:7; color:"#102130"
                ColumnLayout {
                    id:guardDetailContent
                    anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:11; spacing:7
                    Text { text:modelData.channel === "Shared" ? "左右共用濾波器" : modelData.channel + " 聲道濾波器"; color:"#acccda"; font.pixelSize:12; font.weight:Font.DemiBold }
                    Text { text:"固定 80–200 Hz：" + panel.metric((modelData.diagnostic.bass_mean_cut || {}).mean_db,"dB 平均減益") + "\n" + panel.coverageText(modelData.diagnostic.bass_mean_cut || {},true); color:(modelData.diagnostic.bass_mean_cut || {}).coverage_complete === true ? "#94adbf" : "#dfc28c"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                    Text { text:"量測頻段滑動窗最深處：" + panel.metric((modelData.diagnostic.local_cut || {}).max_mean_db,"dB 平均減益") + " · 中心 " + panel.app.num((modelData.diagnostic.local_cut || {}).center_hz,1) + " Hz\n" + panel.coverageText(modelData.diagnostic.local_cut || {},false); color:(modelData.diagnostic.local_cut || {}).coverage_complete === true ? "#94adbf" : "#dfc28c"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                    Text { visible:!!modelData.diagnostic.footprint; text:"完整 PEQ 曲線滑動窗最深處：" + panel.metric(((modelData.diagnostic.footprint || {}).local_cut || {}).max_mean_db,"dB 平均減益") + "\n中心 " + panel.app.num(((modelData.diagnostic.footprint || {}).local_cut || {}).center_hz,1) + " Hz · 頻窗 " + panel.app.num(((modelData.diagnostic.footprint || {}).local_cut || {}).actual_min_hz,1) + "–" + panel.app.num(((modelData.diagnostic.footprint || {}).local_cut || {}).actual_max_hz,1) + " Hz"; color:"#94adbf"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                    Text { visible:!!modelData.diagnostic.footprint; text:"指定校正頻段外：最大 " + panel.metric(((modelData.diagnostic.footprint || {}).outside_cut || {}).max_db,"dB 減益") + " · " + panel.outsideFrequency((modelData.diagnostic.footprint || {}).outside_cut || {}) + "\n指定頻段 " + panel.app.num(((modelData.diagnostic.footprint || {}).outside_cut || {}).requested_min_hz,1) + "–" + panel.app.num(((modelData.diagnostic.footprint || {}).outside_cut || {}).requested_max_hz,1) + " Hz；完整濾波器檢查 " + panel.app.num(((modelData.diagnostic.footprint || {}).range || {}).min_hz,1) + "–" + panel.app.num(((modelData.diagnostic.footprint || {}).range || {}).max_hz,1) + " Hz"; color:"#94adbf"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                    Repeater {
                        model:modelData.diagnostic.records || []
                        delegate:ColumnLayout {
                            required property var modelData
                            Layout.fillWidth:true; spacing:4
                            Text { text:modelData.name || modelData.id || "原始量測"; color:"#a6c4d1"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                            Text { text:"原低處額外減益 " + panel.metric(modelData.below_target_cut_rms_db,"dB RMS") + " · 所有頻點新增低處 " + panel.metric(modelData.added_deficit_rms_db,"dB RMS") + "\n單點新增低處 " + panel.metric(modelData.added_deficit_max_db,"dB") + " · 新落到目標以下 " + (modelData.newly_below_bin_count === undefined ? "—" : modelData.newly_below_bin_count) + " 個頻點"; color:"#7997ae"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                        }
                    }
                }
            }
        }
        Repeater { model:panel.protection.notes || []; delegate:Text { required property string modelData; text:modelData; color:"#7997ae"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 } }
    }
    ColumnLayout {
        visible:panel.showPositions && !!explanation.summary
        Layout.fillWidth:true; spacing:8
        Repeater {
            model:panel.explanation.positions || []
            delegate:Rectangle {
                required property var modelData
                Layout.fillWidth:true; implicitHeight:positionRow.implicitHeight + 18; radius:6; color:"#102130"
                RowLayout {
                    id:positionRow
                    anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:9; spacing:10
                    Text { text:modelData.channel + " · " + modelData.position; color:"#acccda"; font.pixelSize:11; Layout.preferredWidth:65 }
                    ColumnLayout {
                        Layout.fillWidth:true; Layout.preferredWidth:1; spacing:4
                        Text { text:"波峰 · 原始 → 預測（dB RMS）"; color:"#7697ac"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                        Text { text:panel.app.num((modelData.before || {}).residual_peak_rms_db,2) + " → " + panel.app.num((modelData.after || {}).residual_peak_rms_db,2); color:"#c7dfe9"; font.pixelSize:12; font.family:"Segoe UI" }
                    }
                    ColumnLayout {
                        Layout.fillWidth:true; Layout.preferredWidth:1; spacing:4
                        Text { text:"原低點額外減益"; color:"#7697ac"; font.pixelSize:10 }
                        Text { text:panel.app.num((modelData.after || {}).below_target_cut_rms_db,2) + " dB RMS"; color:"#c7dfe9"; font.pixelSize:12; font.family:"Segoe UI" }
                        Text { visible:(modelData.after || {}).below_target_bin_count !== undefined; text:(modelData.after || {}).below_target_bin_count === 0 ? "沒有原低點頻點；0 值不代表安全" : "原低點 " + (modelData.after || {}).below_target_bin_count + " / " + (modelData.after || {}).total_bin_count + " 個頻點"; color:"#7697ac"; font.pixelSize:9; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                    }
                }
            }
        }
    }
    RPButton { objectName:"togglePeqRmse"; text:panel.showRmse ? "收合平坦目標 RMSE" : "對平坦目標的 RMSE"; compact:true; variant:"ghost"; onClicked:panel.showRmse=!panel.showRmse }
    ColumnLayout {
        visible:panel.showRmse
        Layout.fillWidth:true; spacing:7
        Text { text:"對平坦目標的 RMSE：原始 " + app.num((result.metrics || {}).initial_rmse_db,2) + " → 預測 " + app.num((result.metrics || {}).predicted_rmse_db,2) + " dB"; color:"#a1bacb"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap }
        Text { text:"此輔助指標同時計入高於與低於目標的差距，並非求解器唯一的最佳化目標，也不代表聽感改善。"; color:"#7894aa"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
    }
}
