import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ScrollView {
    id: page
    objectName: "peqPage"
    required property var app
    property var s: app.s
    property var peq: app.selectedPeq
    property var saved: (app.project.settings || {}).peq_settings || ({})
    property bool advanced: false
    property string editChannel: ""
    property int editIndex: 0
    property bool editingAllowed: false
    property var comparison: peq.verification || ({})
    function currentSettings() {
        return {bands:Number(bands.text), independent:channelMode.currentIndex === 1,
            f_min:Number(fMin.text), f_max:Number(fMax.text), max_cut:Number(maxCut.text),
            max_total_cut:Number(totalCut.text), min_q:Number(minQ.text), max_q:Number(maxQ.text),
            gain_step:Number(gainStep.text), freq_step:Number(freqStep.text), q_step:Number(qStep.text),
            target_level:target.text.trim() ? Number(target.text) : null,
            mode:searchMode.currentIndex === 1 ? "deep" : "standard", allow_boost:allowBoost.checked,
            max_boost:Number(maxBoost.text), allow_extended:allowExtended.checked, sample_rate:Number(sampleRate.text)};
    }
    function filterRows() {
        var result = [], filters = peq.filters || {};
        var channels = Object.keys(filters);
        for (var c = 0; c < channels.length; c++) {
            var channel = channels[c];
            for (var i = 0; i < filters[channel].length; i++) result.push({channel:channel, index:i, filter:filters[channel][i]});
        }
        return result;
    }
    function startEdit(row) {
        editChannel = row.channel; editIndex = row.index;
        editFrequency.text = String(row.filter.frequency); editGain.text = String(row.filter.gain);
        editQ.text = String(row.filter.q); editEnabled.checked = row.filter.enabled !== false;
        editDialog.open();
    }
    function channelLabel(channel) { return channel === "Shared" ? "共用" : channel === "L" ? "左 L" : channel === "R" ? "右 R" : channel; }
    function inspectVerification(record) {
        var value=record.result || record;
        var lines=[value.title || "驗證比較", "", "比較結果"];
        lines=lines.concat(value.details || []);
        var metrics=value.metrics || {};
        if(metrics.improvement_db !== undefined) {
            lines.push("", "形狀偏差（dB）", "修正前：" + app.num(metrics.shape_error_before_db,2), "補錄後：" + app.num(metrics.shape_error_after_db,2), "改善：" + app.num(metrics.improvement_db,2), "實測與預測偏差：" + app.num(metrics.predicted_agreement_db,2));
        }
        var adjustments=value.adjustments || [];
        if(adjustments.length) lines.push("", "後續調整方向");
        for(var i=0;i<adjustments.length;i++) { var a=adjustments[i]; lines.push(channelLabel(a.channel) + " · " + (a.position || "P0") + " · " + app.num(a.frequency,1) + " Hz", a.suggestion || "", a.reason || "", ""); }
        verificationHistoryDialog.heading=(peq.name || "PEQ") + " · 補錄比較紀錄";
        verificationHistoryDialog.subheading=app.formatTime(value.created_at || record.time) + " · 主圖顯示最近一次比較";
        verificationHistoryDialog.bodyText=lines.join("\n");
        verificationHistoryDialog.open();
    }
    clip: true
    contentWidth: availableWidth
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ColumnLayout {
        width: page.availableWidth
        spacing: 18
        ColumnLayout {
            Layout.fillWidth: true; Layout.leftMargin: 30; Layout.rightMargin: 30; Layout.bottomMargin: 25
            spacing: 18
            RPCard {
                visible: !s.baseline_ready
                Layout.fillWidth: true
                implicitHeight: 100
                RowLayout {
                    anchors.fill: parent; anchors.margins: 20; spacing: 20
                    SectionTitle { title: "先建立可信的 Baseline"; subtitle: "確認量測設定與品質後，再讓 PEQ 針對可重現的低頻凸峰提出建議。"; Layout.fillWidth: true }
                    RPButton { text: "前往量測資料  →"; variant: "primary"; onClicked: app.go(1) }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                spacing: 18
                RPCard {
                    Layout.preferredWidth: 269
                    Layout.minimumWidth: 239
                    Layout.alignment: Qt.AlignTop
                    implicitHeight: controlsColumn.implicitHeight + 38
                    ColumnLayout {
                        id: controlsColumn
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 19
                        spacing: 13
                        SectionTitle { title: "你能調整的 PEQ"; subtitle: "適用硬體、系統 DSP 或播放器。"; Layout.fillWidth: true }
                        Text { text: "可分配的 Band 數"; color: "#91a8bd"; font.pixelSize: 11; Layout.topMargin: 4 }
                        RPField { id: bands; objectName: "peqBands"; Layout.fillWidth: true; text: String(saved.bands || 5); validator: IntValidator {bottom:1; top:20} inputMethodHints: Qt.ImhDigitsOnly }
                        Text { text: "左右聲道的設定方式"; color: "#91a8bd"; font.pixelSize: 11 }
                        RPComboBox { id: channelMode; objectName: "peqChannelMode"; Layout.fillWidth: true; model:["左右共用一組", "左右各自設定"]; currentIndex:saved.independent ? 1 : 0 }
                        Text { text: channelMode.currentIndex === 1 ? "Band 數為每聲道可使用數量。" : "同一組濾波器會同時評估 L / R。"; color: "#708ba3"; font.pixelSize: 10; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        RPCheckBox { id: peakSupported; objectName: "confirmPeakSupport"; text: "支援 Peak / Bell（Hz、dB、Q）"; checked: true; Layout.fillWidth: true }
                        Text { text: "固定頻點的圖示 EQ 不能直接套用。使用頻寬 BW 的工具，需先依該工具規則換算 Q。"; color: "#708ba3"; font.pixelSize: 10; Layout.fillWidth: true; wrapMode: Text.WordWrap; lineHeight: 1.4 }
                        Rectangle { Layout.fillWidth: true; height: 1; color: "#2a3c4e"; Layout.topMargin: 4; Layout.bottomMargin: 3 }
                        Text { text: "校正頻段（Hz）"; color: "#91a8bd"; font.pixelSize: 11 }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 9
                            RPField { id:fMin; objectName:"peqFrequencyMin"; Layout.fillWidth:true; Layout.preferredWidth:1; text:String(saved.f_min || 30); validator:DoubleValidator {bottom:10; top:20000; locale:"C"} }
                            Text { text:"—"; color:"#708ba3" }
                            RPField { id:fMax; objectName:"peqFrequencyMax"; Layout.fillWidth:true; Layout.preferredWidth:1; text:String(saved.f_max || 200); validator:DoubleValidator {bottom:10; top:20000; locale:"C"} }
                        }
                        Text { text: "單段最大減益（dB）"; color: "#91a8bd"; font.pixelSize: 11 }
                        RPField { id:maxCut; objectName:"peqMaxCut"; Layout.fillWidth:true; text:String(saved.max_cut || 6); validator:DoubleValidator {bottom:0.1; top:24; locale:"C"} }
                        Text { text: "搜尋方式"; color: "#91a8bd"; font.pixelSize: 11 }
                        RPComboBox { id:searchMode; objectName:"peqSearchMode"; Layout.fillWidth:true; model:["標準建議", "深入搜尋"]; currentIndex:saved.mode === "deep" ? 1 : 0 }
                        Text { text: searchMode.currentIndex === 1 ? "探索更多候選組合，等待時間較長；不保證能改善所有量測。" : "多起點搜尋與局部精修，優先處理最值得修正的凸峰。"; color:"#708ba3"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                        RPCheckBox { objectName:"peqShowAdvanced"; text: "進階限制與精校"; checked:page.advanced; onClicked:page.advanced=checked; Layout.fillWidth:true }
                        ColumnLayout {
                            visible:page.advanced
                            Layout.fillWidth:true; spacing:11
                            Text { text:"多段總減益上限（dB）"; color:"#91a8bd"; font.pixelSize:11 }
                            RPField { id:totalCut; objectName:"peqTotalCut"; Layout.fillWidth:true; text:String(saved.max_total_cut || 9); validator:DoubleValidator {bottom:0.1; top:36; locale:"C"} }
                            Text { text:"Q 範圍"; color:"#91a8bd"; font.pixelSize:11 }
                            RowLayout {
                                RPField { id:minQ; Layout.fillWidth:true; Layout.preferredWidth:1; text:String(saved.min_q || 0.4); validator:DoubleValidator {bottom:0.05; top:30; locale:"C"} }
                                Text { text:"—"; color:"#708ba3" }
                                RPField { id:maxQ; objectName:"peqMaxQ"; Layout.fillWidth:true; Layout.preferredWidth:1; text:String(saved.max_q || 6); validator:DoubleValidator {bottom:0.05; top:30; locale:"C"} }
                            }
                            Text { text:"可輸入步進：Hz / dB / Q"; color:"#91a8bd"; font.pixelSize:11 }
                            RowLayout {
                                spacing:6
                                RPField { id:freqStep; Layout.fillWidth:true; Layout.preferredWidth:1; text:String(saved.freq_step || 1); validator:DoubleValidator {bottom:0.01; top:1000; locale:"C"} }
                                RPField { id:gainStep; Layout.fillWidth:true; Layout.preferredWidth:1; text:String(saved.gain_step || 0.1); validator:DoubleValidator {bottom:0.01; top:6; locale:"C"} }
                                RPField { id:qStep; Layout.fillWidth:true; Layout.preferredWidth:1; text:String(saved.q_step || 0.01); validator:DoubleValidator {bottom:0.001; top:2; locale:"C"} }
                            }
                            Text { text:"目標水平（dB，留空為自動）"; color:"#91a8bd"; font.pixelSize:11 }
                            RPField { id:target; objectName:"peqTargetLevel"; Layout.fillWidth:true; text:saved.target_level !== undefined && saved.target_level !== null ? String(saved.target_level) : ""; placeholderText:"自動估計"; validator:DoubleValidator {bottom:-150; top:180; locale:"C"} }
                            Text { text:"DSP 取樣率（Hz）"; color:"#91a8bd"; font.pixelSize:11 }
                            RPField { id:sampleRate; Layout.fillWidth:true; text:String(saved.sample_rate || 48000); validator:IntValidator {bottom:8000; top:768000} }
                            RPCheckBox { id:allowExtended; objectName:"peqAllowExtended"; text:"允許校正 200 Hz 以上"; checked:!!saved.allow_extended; Layout.fillWidth:true }
                            RPCheckBox { id:allowBoost; objectName:"peqAllowBoost"; text:"允許增益（進階精校）"; checked:!!saved.allow_boost; Layout.fillWidth:true }
                            RPField { id:maxBoost; visible:allowBoost.checked; Layout.fillWidth:true; text:String(saved.max_boost || 3); placeholderText:"最大增益 dB"; validator:DoubleValidator {bottom:0; top:6; locale:"C"} }
                            Text { visible:allowBoost.checked || allowExtended.checked; text:"先取得同點補錄與左右偏移量測，再評估寬頻一致性；深窄凹洞通常不適合補償。增益方案需預留前級衰減。"; color:"#dfc28c"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                        }
                        RPButton { objectName:"generatePeq"; text:"產生 PEQ 建議  →"; variant:"primary"; Layout.fillWidth:true; enabled:!!s.baseline_ready && !s.busy && peakSupported.checked; onClicked:bridge.generatePeq(JSON.stringify(page.currentSettings())) }
                        Text { text:"先做低頻減益，不強迫填滿 Band。每次生成的是可取代舊版的完整方案。"; color:"#6f899f"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                    }
                }
                ColumnLayout {
                    Layout.fillWidth:true
                    Layout.preferredWidth:650
                    Layout.alignment:Qt.AlignTop
                    spacing:17
                    RPCard {
                        Layout.fillWidth:true
                        implicitHeight:curveColumn.implicitHeight + 36
                        ColumnLayout {
                            id:curveColumn
                            anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:18
                            spacing:14
                            RowLayout {
                                Layout.fillWidth:true; spacing:12
                                RPComboBox { objectName:"peqVersionSelector"; Layout.fillWidth:true; model:s.peqs || []; textRole:"name"; displayText:peq.name || "尚未產生方案"; onActivated:function(index){bridge.selectPeq(model[index].id)} }
                                Rectangle { visible:!!peq.id; implicitWidth:statusText.implicitWidth + 16; height:25; radius:6; color:"#1c373d"; Text { id:statusText; anchors.centerIn:parent; text:app.statusLabel(peq.status); color:"#83dbc5"; font.pixelSize:10 } }
                                RPCheckBox { text:"低頻"; checked:true; onCheckedChanged:peqChart.lowOnly=checked }
                            }
                            RowLayout {
                                visible:!!peq.id && (peq.metrics || {}).initial_rmse_db !== undefined
                                Layout.fillWidth:true; spacing:9
                                Repeater {
                                    model:[
                                        {label:"修正前偏差",value:(peq.metrics || {}).initial_rmse_db},
                                        {label:"預測偏差",value:(peq.metrics || {}).predicted_rmse_db},
                                        {label:"預測改善",value:(peq.metrics || {}).improvement_db}
                                    ]
                                    delegate:Rectangle {
                                        required property var modelData
                                        required property int index
                                        Layout.fillWidth:true; Layout.preferredWidth:1; height:68; radius:7; color:"#102130"
                                        ColumnLayout {
                                            anchors.fill:parent; anchors.margins:11; spacing:3
                                            Text { text:modelData.label; color:"#7997ae"; font.pixelSize:10 }
                                            Text { text:app.num(modelData.value,2) + " dB"; color:index === 2 ? "#83dbc5" : "#d2e2ed"; font.pixelSize:18; font.family:"Segoe UI"; font.weight:Font.DemiBold }
                                        }
                                    }
                                }
                            }
                            FrequencyChart { id:peqChart; objectName:"peqPredictionChart"; Layout.fillWidth:true; Layout.preferredHeight:278; lowOnly:true; curves:peq.id ? ((s.chart || {}).curves || []) : []; frequencyMin:(s.chart || {}).f_min || 20; frequencyMax:(s.chart || {}).f_max || 20000; emptyText:"設定左側 PEQ 能力，產生第一版建議" }
                            Text { visible:!!peq.id; text:comparison.title ? "補錄實測保留原始音量；如有音量對齊曲線，會另以長虛線呈現。偏差數值為原方案在校正頻段對目標的預測。" : "虛線：Baseline ／ 實線：PEQ 預測 ／ 金色虛線：目標。偏差為校正頻段內對目標的 RMS 誤差，需補錄驗證。"; color:"#7893aa"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                        }
                    }
                    RPCard {
                        visible:!!peq.id
                        Layout.fillWidth:true
                        implicitHeight:filterColumn.implicitHeight + 36
                        ColumnLayout {
                            id:filterColumn
                            anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:18
                            spacing:12
                            RowLayout {
                                Text { text:"完整 PEQ 參數"; color:"#dce8f2"; font.pixelSize:16; font.weight:Font.DemiBold; Layout.fillWidth:true }
                                RPButton { objectName:"exportPeqText"; text:"匯出文字"; compact:true; onClicked:bridge.exportPeq(peq.id,"text") }
                                RPButton { objectName:"exportPeqCsv"; text:"CSV"; compact:true; onClicked:bridge.exportPeq(peq.id,"csv") }
                            }
                            Rectangle {
                                Layout.fillWidth:true; height:32; radius:6; color:"#1a2d3f"
                                RowLayout {
                                    anchors.fill:parent; anchors.leftMargin:10; anchors.rightMargin:10; spacing:5
                                    Text { text:"聲道"; color:"#809ab1"; font.pixelSize:10; Layout.preferredWidth:45 }
                                    Text { text:"Band"; color:"#809ab1"; font.pixelSize:10; Layout.preferredWidth:37 }
                                    Text { text:"類型"; color:"#809ab1"; font.pixelSize:10; Layout.preferredWidth:40 }
                                    Text { text:"頻率 Hz"; color:"#809ab1"; font.pixelSize:10; Layout.fillWidth:true; horizontalAlignment:Text.AlignRight }
                                    Text { text:"Gain dB"; color:"#809ab1"; font.pixelSize:10; Layout.preferredWidth:65; horizontalAlignment:Text.AlignRight }
                                    Text { text:"Q"; color:"#809ab1"; font.pixelSize:10; Layout.preferredWidth:49; horizontalAlignment:Text.AlignRight }
                                    Item { Layout.preferredWidth:56 }
                                }
                            }
                            Repeater {
                                model:page.filterRows()
                                delegate:Rectangle {
                                    required property var modelData
                                    Layout.fillWidth:true
                                    height:42; radius:5; color:"#102030"
                                    opacity:modelData.filter.enabled === false ? 0.5 : 1
                                    RowLayout {
                                        anchors.fill:parent; anchors.leftMargin:10; anchors.rightMargin:5; spacing:5
                                        Text { text:page.channelLabel(modelData.channel); color:modelData.channel === "R" ? "#94b2df" : "#83dbc5"; font.pixelSize:11; Layout.preferredWidth:45 }
                                        Text { text:String(modelData.index + 1); color:"#b8cedd"; font.pixelSize:12; font.family:"Segoe UI"; Layout.preferredWidth:37 }
                                        Text { text:"Peak"; color:"#8ba4b9"; font.pixelSize:10; Layout.preferredWidth:40 }
                                        Text { text:app.num(modelData.filter.frequency,1); color:"#d5e5ef"; font.pixelSize:12; font.family:"Segoe UI"; Layout.fillWidth:true; horizontalAlignment:Text.AlignRight }
                                        Text { text:(modelData.filter.gain > 0 ? "+" : "") + app.num(modelData.filter.gain,1); color:modelData.filter.gain > 0 ? "#e4c487" : "#d5e5ef"; font.pixelSize:12; font.family:"Segoe UI"; Layout.preferredWidth:65; horizontalAlignment:Text.AlignRight }
                                        Text { text:app.num(modelData.filter.q,2); color:"#d5e5ef"; font.pixelSize:12; font.family:"Segoe UI"; Layout.preferredWidth:49; horizontalAlignment:Text.AlignRight }
                                        RPButton { text:"編輯"; objectName:"editFilter_" + modelData.channel + "_" + modelData.index; compact:true; variant:"ghost"; implicitWidth:56; onClicked:page.startEdit(modelData) }
                                    }
                                }
                            }
                            Text { visible:page.filterRows().length === 0; text:"目前條件下沒有值得加入的濾波器；保留現況也是有效的建議。"; color:"#9bb5c5"; font.pixelSize:12; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                            Rectangle {
                                Layout.fillWidth:true; implicitHeight:preampRow.implicitHeight + 20; radius:7; color:"#1a3139"
                                RowLayout {
                                    id:preampRow
                                    anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:10; spacing:12
                                    Text { text:"前級衰減"; color:"#88bbaa"; font.pixelSize:11 }
                                    Text { text:app.num(peq.preamp_db || 0,1) + " dB"; color:"#d9f1e9"; font.pixelSize:16; font.weight:Font.DemiBold; font.family:"Segoe UI" }
                                    Text { text:"請取代上一版參數，避免重複疊加。"; color:"#7d9eae"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; horizontalAlignment:Text.AlignRight }
                                }
                            }
                            RPButton { objectName:"markPeqApplied"; text:peq.status === "applied" || peq.status === "verified" ? "✓  已確認套用此版本" : "我已在 DSP 套用此版本"; variant:"primary"; Layout.fillWidth:true; enabled:!s.busy && peq.status !== "applied" && peq.status !== "verified"; onClicked:appliedDialog.open() }
                        }
                    }
                    RPCard {
                        visible:!!peq.id
                        Layout.fillWidth:true
                        implicitHeight:rationaleColumn.implicitHeight + 36
                        ColumnLayout {
                            id:rationaleColumn
                            anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:18
                            spacing:12
                            Text { text:"建議依據"; color:"#dce8f2"; font.pixelSize:15; font.weight:Font.DemiBold }
                            Repeater { model:peq.rationale || []; delegate:Text { required property string modelData; text:"•  " + modelData; color:"#a3bacc"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.45 } }
                            Repeater { model:peq.warnings || []; delegate:Text { required property string modelData; text:"!  " + modelData; color:"#dfc28c"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.45 } }
                        }
                    }
                    RPCard {
                        visible:!!peq.id
                        Layout.fillWidth:true
                        implicitHeight:verificationColumn.implicitHeight + 38
                        ColumnLayout {
                            id:verificationColumn
                            anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:19
                            spacing:14
                            SectionTitle { title:"套用後，再量一次"; subtitle:"先以相同音量與播放路徑，在 P0 重錄 L/R；再向左右各偏移 10 cm，檢查聆聽區域的一致性。"; Layout.fillWidth:true }
                            Text { text:"確認麥克風 Cal 已載入，且量測訊號確實經過這版 DSP。偏移後仍各錄獨立 L/R；匯入後在量測資料頁確認位置。"; color:"#89a2b9"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                            RowLayout {
                                Layout.fillWidth:true; spacing:10
                                RPButton { objectName:"importVerification"; text:"+  匯入補錄量測"; variant:"primary"; enabled:!s.busy && (peq.status === "applied" || peq.status === "verified"); onClicked:bridge.importFiles("verification",peq.id) }
                                RPButton { objectName:"compareVerification"; text:"比較並取得下一步"; enabled:!s.busy && (peq.status === "applied" || peq.status === "verified"); onClicked:bridge.compareVerification(peq.id) }
                            }
                            Text { visible:peq.status !== "applied" && peq.status !== "verified"; text:"確認套用上方版本後，即可綁定補錄。"; color:"#dfc28c"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                            Rectangle { visible:!!comparison.title; Layout.fillWidth:true; height:1; color:"#2a3e50"; Layout.topMargin:3 }
                            Text { visible:!!comparison.title; text:comparison.title || ""; color:"#cce9df"; font.pixelSize:14; font.weight:Font.DemiBold; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                            Repeater { model:comparison.details || []; delegate:Text { required property string modelData; text:modelData; color:"#98b2c6"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.45 } }
                            Repeater {
                                model:comparison.adjustments || []
                                delegate:Rectangle {
                                    required property var modelData
                                    Layout.fillWidth:true
                                    implicitHeight:adjustmentColumn.implicitHeight + 22
                                    radius:7; color:"#102331"
                                    ColumnLayout {
                                        id:adjustmentColumn
                                        anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:11; spacing:6
                                        Text { text:page.channelLabel(modelData.channel) + " · " + (modelData.position || "P0") + " · " + app.num(modelData.frequency,1) + " Hz"; color:"#8cccb8"; font.pixelSize:11 }
                                        Text { text:modelData.suggestion || ""; color:"#c0d9e5"; font.pixelSize:12; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                                        Text { text:modelData.reason || ""; color:"#7e9caf"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                                    }
                                }
                            }
                            Rectangle { visible:(peq.verification_history || []).length > 0; Layout.fillWidth:true; height:1; color:"#2b4052"; Layout.topMargin:5 }
                            Text { visible:(peq.verification_history || []).length > 0; text:"歷次補錄比較 · 主圖顯示最近一次"; color:"#abc9d9"; font.pixelSize:12; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                            Repeater {
                                model:peq.verification_history || []
                                delegate:RowLayout {
                                    required property var modelData
                                    required property int index
                                    Layout.fillWidth:true; spacing:10
                                    ColumnLayout {
                                        Layout.fillWidth:true; spacing:4
                                        Text { text:modelData.title || (modelData.result || {}).title || "補錄比較"; color:"#a8c2d2"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                                        Text { text:app.formatTime(modelData.created_at || modelData.time); color:"#6e8da5"; font.pixelSize:10 }
                                    }
                                    RPButton { objectName:"inspectVerification_" + index; text:"查看"; compact:true; variant:"ghost"; onClicked:page.inspectVerification(modelData) }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    Dialog {
        id:editDialog
        objectName:"editFilterDialog"
        parent:Overlay.overlay
        anchors.centerIn:parent
        width:460; modal:true; padding:25
        background:Rectangle { color:"#142335"; radius:14; border.color:"#3a5063" }
        contentItem:ColumnLayout {
            spacing:16
            SectionTitle { title:"編輯 " + page.channelLabel(page.editChannel) + " · Band " + (page.editIndex + 1); subtitle:"儲存後重新計算預測，並保留原方案供回顧。"; Layout.fillWidth:true }
            RowLayout {
                Layout.fillWidth:true; spacing:12
                ColumnLayout { Layout.fillWidth:true; Layout.preferredWidth:1; Text { text:"頻率 Hz"; color:"#91a8bd"; font.pixelSize:11 } RPField { id:editFrequency; objectName:"editFilterFrequency"; Layout.fillWidth:true; validator:DoubleValidator {bottom:10; top:20000; locale:"C"} } }
                ColumnLayout { Layout.fillWidth:true; Layout.preferredWidth:1; Text { text:"Gain dB"; color:"#91a8bd"; font.pixelSize:11 } RPField { id:editGain; objectName:"editFilterGain"; Layout.fillWidth:true; validator:DoubleValidator {bottom:-24; top:12; locale:"C"} } }
                ColumnLayout { Layout.fillWidth:true; Layout.preferredWidth:1; Text { text:"Q"; color:"#91a8bd"; font.pixelSize:11 } RPField { id:editQ; objectName:"editFilterQ"; Layout.fillWidth:true; validator:DoubleValidator {bottom:0.05; top:30; locale:"C"} } }
            }
            RPCheckBox { id:editEnabled; text:"啟用這個濾波器"; checked:true }
            RowLayout {
                Layout.fillWidth:true; Item { Layout.fillWidth:true }
                RPButton { text:"取消"; onClicked:editDialog.close() }
                RPButton { objectName:"saveFilterEdit"; text:"儲存並重新驗算"; variant:"primary"; enabled:editFrequency.acceptableInput && editGain.acceptableInput && editQ.acceptableInput; onClicked:{bridge.editFilter(page.editChannel,page.editIndex,Number(editFrequency.text),Number(editGain.text),Number(editQ.text),editEnabled.checked);editDialog.close()} }
            }
        }
    }
    Dialog {
        id:appliedDialog
        objectName:"markAppliedDialog"
        parent:Overlay.overlay
        anchors.centerIn:parent
        width:500; modal:true; padding:25
        background:Rectangle { color:"#142335"; radius:14; border.color:"#3a5063" }
        contentItem:ColumnLayout {
            spacing:17
            SectionTitle { title:"確認 PEQ 已套用"; subtitle:peq.name || ""; Layout.fillWidth:true }
            Text { text:"請核對所有聲道的頻率、Gain、Q 與前級衰減，取代舊版濾波器，並確認 DSP 已啟用。這個操作只記錄你的確認，不會直接控制音訊設備。"; color:"#a7bfd0"; font.pixelSize:12; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.5 }
            RowLayout {
                Layout.fillWidth:true; Item { Layout.fillWidth:true }
                RPButton { text:"返回檢查"; onClicked:appliedDialog.close() }
                RPButton { objectName:"confirmPeqApplied"; text:"已核對並套用"; variant:"primary"; onClicked:{bridge.markApplied(peq.id);appliedDialog.close()} }
            }
        }
    }
    InspectDialog { id:verificationHistoryDialog; objectName:"verificationHistoryDialog" }
}
