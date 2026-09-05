import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ScrollView {
    id: page
    objectName: "overviewPage"
    required property var app
    property var s: app.s
    property var settings: app.project.settings || ({})
    property var checklist: settings.checklist || ({})
    property int sopStep: 0
    property var environmentItems: [
        {key:"quiet", title:"暫時關閉冷氣、除濕機與風扇", detail:"減少背景噪音，避免氣流直接吹向麥克風。"},
        {key:"notifications", title:"暫停音樂、通知聲與談話", detail:"量測期間保持安靜，不要走動或碰觸桌面。"},
        {key:"mic_position", title:"腳架固定麥克風，置於耳朵高度", detail:"以 P0 標記中央聆聽位置，記下高度、朝向與距離。"},
        {key:"room_state", title:"固定門窗、窗簾與家具狀態", detail:"選擇日常聆聽條件，後續補錄維持相同狀態。"},
        {key:"processing", title:"記錄音量與所有音效處理", detail:"建立 Baseline 前確認既有 EQ、Loudness、音調控制及系統音效狀態。"},
        {key:"channels", title:"確認左、右聲道對應正確", detail:"單獨播放左聲道時，應只有左側喇叭發聲；右聲道亦同。"}
    ]
    property var rewItems: [
        {key:"rew_io", title:"選擇正確的輸入與播放輸出", detail:"REW Preferences → Soundcard：選擇量測麥克風與實際播放裝置，並確認通道。"},
        {key:"rew_cal", title:"載入麥克風 Cal，核對序號與朝向", detail:"Mic/Meter 設定使用對應麥克風的校正檔；0°／90° 要與實際朝向相符。Measure 視窗再次確認 Calibration files。"},
        {key:"rew_spl", title:"確認 SPL 聲壓校正，不只載入頻響 Cal", detail:"USB 麥克風 Cal 含 Sens Factor，且 REW 支援該裝置時，可依靈敏度自動校正。否則在 SPL Meter → Calibrate，依提示輸入外部聲壓計或聲壓校正器的參考讀值，勿填 REW 自己的讀值。沒有可靠參考時只視為相對電平；改動輸入或增益後重新確認。"},
        {key:"rew_sweep", title:"設定掃頻頻段、長度與取樣率", detail:"完整診斷可從 20 Hz–20 kHz、128k 或 256k、裝置支援的 48 kHz 起步；依喇叭安全頻段調整。PEQ 預設只校正低頻。"},
        {key:"rew_levels", title:"先降低音量，再執行 Check Levels", detail:"掃頻輸出可由 −12 dBFS 起步，逐步調整到乾淨且不過載的量測。dBFS 是數位電平，不等於現場 dB SPL。勿為追求讀值而過度提高音量。"},
        {key:"rew_fixed", title:"保存並固定設定", detail:"確認沒有 clipping；將音量、Cal、頻段、麥克風位置記錄下來，A/B 與後續補錄沿用。"}
    ]
    property var recordingItems: [
        {key:"record_l", title:"P0 左聲道，各錄兩筆", detail:"建議命名 L_P0_A、L_P0_B。兩筆獨立掃頻能檢查重錄的一致性。"},
        {key:"record_r", title:"保持位置不動，錄右聲道兩筆", detail:"建議命名 R_P0_A、R_P0_B。L+R 同時播放可額外錄製，但不能取代獨立聲道量測。"},
        {key:"record_save", title:"儲存全部量測為 .mdat", detail:"File → Save all measurements。匯入後再確認聲道、位置、用途與 Cal 提醒。"},
        {key:"record_dsp", title:"若使用播放器 DSP，確認掃頻經過它", detail:"REW 直接輸出可能繞過 Roon 等播放器的 PEQ。可依 REW 指引使用含 timing reference 的掃頻檔播放量測；校正前後保持相同路徑。"}
    ]
    clip: true
    contentWidth: availableWidth
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ColumnLayout {
        width: page.availableWidth
        spacing: 20
        ColumnLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 30; Layout.rightMargin: 30; Layout.bottomMargin: 26
            spacing: 20
            RPCard {
                visible: !app.hasProject
                Layout.fillWidth: true
                implicitHeight: 263
                color: "#172c3b"
                Rectangle { anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom; width: parent.width * 0.3; radius: 12; color: "#1b3541" }
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 30; spacing: 14
                    Text { text: "LISTEN. MEASURE. REFINE."; color: "#83dbc5"; font.pixelSize: 10; font.letterSpacing: 2; font.family: "Segoe UI" }
                    Text { text: "讓空間校正，\n從一筆可靠的量測開始。"; color: "#f0f7f8"; font.pixelSize: 29; font.weight: Font.DemiBold; lineHeight: 1.2; Layout.fillWidth: true }
                    Text { text: "建立 Baseline，取得保守的低頻 PEQ，再用實測驗證每一次調整。"; color: "#a8bdca"; font.pixelSize: 13; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    RowLayout {
                        Layout.topMargin: 5; spacing: 12
                        RPButton { objectName: "welcomeNewProject"; text: "+  建立第一個專案"; variant: "primary"; onClicked: app.openNewProject() }
                        RPButton { text: "開啟專案包"; onClicked: bridge.importProject() }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 14
                Repeater {
                    model: [
                        {label:"量測資料", value: String((s.measurements || []).length), suffix:"筆", detail:"原始資料與條件完整保留"},
                        {label:"基準版本", value: s.baseline_ready ? "v" + (app.project.baseline_version || 1) : "—", suffix:"", detail:s.baseline_ready ? "已選定 Baseline 量測組" : "匯入後完成品質檢查"},
                        {label:"PEQ 方案", value:String((s.peqs || []).length), suffix:"版", detail:app.selectedPeq.id ? app.selectedPeq.name + " · " + app.statusLabel(app.selectedPeq.status) : "低頻優先 · 僅減益起步"}
                    ]
                    delegate: RPCard {
                        required property var modelData
                        Layout.fillWidth: true; Layout.preferredWidth: 1
                        implicitHeight: 128
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 19; spacing: 6
                            Text { text: modelData.label; color: "#8fa5b9"; font.pixelSize: 12 }
                            RowLayout { spacing: 6; Text { text: modelData.value; color: "#e4eff5"; font.pixelSize: 28; font.family: "Segoe UI"; font.weight: Font.DemiBold } Text { text: modelData.suffix; color: "#849db3"; font.pixelSize: 11; Layout.alignment: Qt.AlignBottom; Layout.bottomMargin: 5 } }
                            Text { text: modelData.detail; color: "#738ca2"; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideRight }
                        }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                spacing: 18
                RPCard {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 640
                    Layout.alignment: Qt.AlignTop
                    implicitHeight: sopColumn.implicitHeight + 42
                    ColumnLayout {
                        id: sopColumn
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 21
                        spacing: 16
                        SectionTitle { title: "量測準備指南"; subtitle: "三個步驟建立可重現的量測。勾選進度會隨專案保存。"; Layout.fillWidth: true }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 7
                            Repeater {
                                model: ["01  環境準備", "02  REW 設定", "03  正式量測"]
                                delegate: RPButton {
                                    required property string modelData
                                    required property int index
                                    Layout.fillWidth: true
                                    text: modelData
                                    compact: true
                                    variant: page.sopStep === index ? "primary" : "secondary"
                                    onClicked: page.sopStep = index
                                }
                            }
                        }
                        Repeater {
                            model: page.sopStep === 0 ? page.environmentItems : page.sopStep === 1 ? page.rewItems : page.recordingItems
                            delegate: ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 1
                                RPCheckBox {
                                    objectName: "checklist_" + modelData.key
                                    text: modelData.title
                                    Layout.fillWidth: true
                                    checked: !!page.checklist[modelData.key]
                                    enabled: app.hasProject
                                    onClicked: bridge.updateChecklist(modelData.key, checked)
                                }
                                Text { text: modelData.detail; color: "#849bb1"; font.pixelSize: 11; wrapMode: Text.WordWrap; lineHeight: 1.35; Layout.fillWidth: true; Layout.leftMargin: 29 }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Layout.topMargin: 4
                            RPButton { text: page.sopStep === 1 ? "REW 設定說明 ↗" : page.sopStep === 2 ? "檔案播放量測 ↗" : "下載 REW ↗"; variant: "ghost"; compact: true; onClicked: bridge.openLink(page.sopStep === 1 ? "rew_setup" : page.sopStep === 2 ? "rew_fileplayback" : "rew") }
                            RPButton { visible: page.sopStep === 1; text: "SPL / Cal 指引 ↗"; variant: "ghost"; compact: true; onClicked: bridge.openLink("rew_cal") }
                            Item { Layout.fillWidth: true }
                            RPButton { text: page.sopStep < 2 ? "下一步  →" : "匯入量測  →"; variant: "primary"; compact: true; enabled: page.sopStep < 2 || (app.hasProject && !s.busy); onClicked: page.sopStep < 2 ? page.sopStep++ : bridge.importFiles("baseline", "") }
                        }
                    }
                }
                ColumnLayout {
                    Layout.preferredWidth: 285
                    Layout.minimumWidth: 235
                    Layout.alignment: Qt.AlignTop
                    spacing: 18
                    RPCard {
                        Layout.fillWidth: true
                        implicitHeight: deviceColumn.implicitHeight + 40
                        ColumnLayout {
                            id: deviceColumn
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 20
                            spacing: 11
                            SectionTitle { title: "音訊裝置"; subtitle: "記錄量測路徑；PEQ 功能另行設定。"; Layout.fillWidth: true }
                            RPButton { objectName: "refreshDevices"; text: "重新偵測本機裝置"; compact: true; Layout.fillWidth: true; enabled: !s.busy; onClicked: bridge.refreshDevices() }
                            Text { text: "錄音輸入 / 麥克風"; color: "#91a7bb"; font.pixelSize: 11; Layout.topMargin: 5 }
                            RPComboBox {
                                objectName: "inputDeviceSelector"
                                Layout.fillWidth: true
                                model: (s.devices || {}).inputs || []
                                displayText: settings.input_device || "選擇錄音裝置"
                                enabled: app.hasProject && count > 0
                                onActivated: function(index) { bridge.updateSettings(JSON.stringify({input_device:model[index]})); }
                            }
                            Text { text: "播放輸出"; color: "#91a7bb"; font.pixelSize: 11 }
                            RPComboBox {
                                objectName: "outputDeviceSelector"
                                Layout.fillWidth: true
                                model: (s.devices || {}).outputs || []
                                displayText: settings.output_device || "選擇播放裝置"
                                enabled: app.hasProject && count > 0
                                onActivated: function(index) { bridge.updateSettings(JSON.stringify({output_device:model[index]})); }
                            }
                            Text { text: "偵測到的名稱不代表 REW 當時使用的裝置；匯入後會另外顯示檔案記錄。"; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#6f899f"; font.pixelSize: 10; lineHeight: 1.4 }
                            Text { text: "PEQ 套用位置（選填）"; color: "#91a7bb"; font.pixelSize: 11; Layout.topMargin: 4 }
                            RPField { objectName: "dspLocation"; Layout.fillWidth: true; placeholderText: "例如：Roon / 系統 DSP / 硬體"; text: settings.peq_destination || ""; enabled: app.hasProject; onEditingFinished: bridge.updateSettings(JSON.stringify({peq_destination:text})) }
                        }
                    }
                    RPCard {
                        Layout.fillWidth: true
                        implicitHeight: notesColumn.implicitHeight + 36
                        ColumnLayout {
                            id: notesColumn
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18
                            spacing: 10
                            Text { text: "量測條件備註"; color: "#d7e5ee"; font.pixelSize: 14; font.weight: Font.DemiBold }
                            Text { text: "麥克風朝向 / Cal 類型"; color: "#91a7bb"; font.pixelSize: 11 }
                            RPComboBox {
                                objectName: "micOrientation"
                                Layout.fillWidth: true
                                model: ["尚未確認", "0° · 指向喇叭", "90° · 垂直朝上", "其他 / 詳見備註"]
                                displayText: settings.mic_orientation || "尚未確認"
                                enabled: app.hasProject
                                onActivated: function(index) { bridge.updateSettings(JSON.stringify({mic_orientation:model[index]})); }
                            }
                            Text { text: "主音量 / 前級增益"; color: "#91a7bb"; font.pixelSize: 11 }
                            RPField { objectName: "volumeNote"; Layout.fillWidth: true; placeholderText: "例如：主音量 −25 dB，增益固定"; text: settings.volume_note || ""; enabled: app.hasProject; onEditingFinished: bridge.updateSettings(JSON.stringify({volume_note:text})) }
                            Text { text: "播放路徑 / DSP 狀態"; color: "#91a7bb"; font.pixelSize: 11 }
                            RPField { objectName: "routeNote"; Layout.fillWidth: true; placeholderText: "例如：REW → USB DAC，原有 EQ 關"; text: settings.route_note || ""; enabled: app.hasProject; onEditingFinished: bridge.updateSettings(JSON.stringify({route_note:text})) }
                            Text { text: "匯入量測時保存這些條件，補錄前請核對；朝向仍需與實際 Cal 相符。"; color: "#718da3"; font.pixelSize: 10; Layout.fillWidth: true; wrapMode: Text.WordWrap; lineHeight: 1.4 }
                            TextArea {
                                id: conditionNotes
                                objectName: "measurementConditions"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 125
                                text: settings.room_note || app.project.notes || ""
                                enabled: app.hasProject
                                color: "#b6c7d5"; font.pixelSize: 11; wrapMode: TextEdit.Wrap; selectByMouse: true; padding: 10
                                placeholderText: "麥克風高度／朝向、主音量、房間狀態、播放路徑…"; placeholderTextColor: "#6e859a"
                                background: Rectangle { color: "#0e1a29"; radius: 7; border.color: "#2d4053" }
                            }
                            RPButton { text: "儲存備註"; compact: true; Layout.alignment: Qt.AlignRight; enabled: app.hasProject; onClicked: bridge.updateSettings(JSON.stringify({room_note:conditionNotes.text})) }
                        }
                    }
                }
            }
        }
    }
}
