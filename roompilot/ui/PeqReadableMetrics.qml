import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: panel
    required property var app
    property var result: ({})
    property var explanation:result.explanation || ({})
    property var before:(explanation.summary || {}).before || ({})
    property var after:(explanation.summary || {}).after || ({})
    property bool shapeMode: ((explanation.evaluation_policy || {}).objective_mode || (result.settings || {}).objective_mode) === "shape"
    property bool showPositions:false
    property bool showRmse:false
    spacing:10
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
                Text { text:app.num(panel.after.worst_below_target_cut_rms_db,2) + " dB RMS"; color:"#d2e2ed"; font.pixelSize:18; font.family:"Segoe UI"; font.weight:Font.DemiBold }
                Text { text:"各位置 RMS 的最大值"; color:"#7997ae"; font.pixelSize:10 }
            }
        }
    }
    Text { visible:!!explanation.summary; text:panel.shapeMode ? "精修誤差計入殘留波峰與具多位置支持的寬低處；深窄凹洞不因此獲得增益。殘留波峰另為 " + app.num(panel.after.residual_peak_rms_db,2) + " dB RMS。額外減益只取原先低於目標的頻點，與主目標不能相加。" : "殘留波峰使用整個校正頻段；額外減益只取原先低於目標的頻點。分母不同，不能相加；位置最大 RMS 也不是單一頻點的最大衰減。"; color:"#7997ae"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
    Text { visible:!explanation.summary; text:"此舊版本未保存這兩項可讀指標；可產生新候選重新分析。"; color:"#9ab0c0"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap }
    RowLayout {
        Layout.fillWidth:true; spacing:10
        RPButton { objectName:"togglePeqPositionMetrics"; visible:!!explanation.summary; text:panel.showPositions ? "收合各位置" : "展開各聲道／位置"; compact:true; variant:"ghost"; onClicked:panel.showPositions=!panel.showPositions }
        RPButton { objectName:"togglePeqRmse"; text:panel.showRmse ? "收合平坦目標 RMSE" : "對平坦目標的 RMSE"; compact:true; variant:"ghost"; onClicked:panel.showRmse=!panel.showRmse }
        Item { Layout.fillWidth:true }
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
    ColumnLayout {
        visible:panel.showRmse
        Layout.fillWidth:true; spacing:7
        Text { text:"對平坦目標的 RMSE：原始 " + app.num((result.metrics || {}).initial_rmse_db,2) + " → 預測 " + app.num((result.metrics || {}).predicted_rmse_db,2) + " dB"; color:"#a1bacb"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap }
        Text { text:"此輔助指標同時計入高於與低於目標的差距，並非求解器唯一的最佳化目標，也不代表聽感改善。"; color:"#7894aa"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
    }
}
