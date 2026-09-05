import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ScrollView {
    id: page
    objectName: "measurementsPage"
    required property var app
    property var s: app.s
    property var selected: s.selected_measurement || ({})
    property var meta: selected.metadata || ({})
    property var chosenIds: defaultSelection()
    property string projectId: app.project.id || ""
    property bool showAllQuality: false
    onProjectIdChanged: chosenIds = Qt.binding(page.defaultSelection)
    function defaultSelection() { return (app.project.baseline_ids || []).length ? app.project.baseline_ids : (s.measurements || []).filter(function(m) { return m.role === "baseline" && m.position === "P0" && (m.channel === "L" || m.channel === "R"); }).map(function(m) { return m.id; }); }
    function eligible(m) { return m.role === "baseline"; }
    function toggle(id, checked) { var next = chosenIds.slice(); var i = next.indexOf(id); if (checked && i < 0) next.push(id); if (!checked && i >= 0) next.splice(i,1); chosenIds = next; }
    function calLabel(m) { var value = (m.metadata || {}).cal_status || m.cal_status || "unknown"; return value === "loaded" ? "Cal 已載入" : value === "missing" ? "未載入 Cal" : "Cal 未確認"; }
    function calColor(m) { var value = (m.metadata || {}).cal_status || m.cal_status || "unknown"; return value === "loaded" ? "#83dbc5" : value === "missing" ? "#f2b089" : "#a7b6c8"; }
    function qualityRows() { var rows = selected.quality || []; if (!rows.length) rows = (s.quality || []).filter(function(q) { return !(q.measurement_ids || []).length || (q.measurement_ids || []).indexOf(selected.id) >= 0; }); return showAllQuality ? rows : rows.filter(function(q) { return q.level !== "pass"; }); }
    function levelName(level) { return ({pass:"已確認",warning:"需要注意",error:"需要處理",unknown:"資料不足"})[level] || "待確認"; }
    function levelColor(level) { return level === "pass" ? "#83dbc5" : level === "error" ? "#ffb4a7" : level === "warning" ? "#e4c487" : "#90a7bd"; }
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
                Layout.fillWidth: true
                implicitHeight: Math.max(91, importHelp.implicitHeight + 36)
                RowLayout {
                    anchors.fill: parent; anchors.margins: 18; spacing: 18
                    ColumnLayout {
                        id: importHelp
                        Layout.fillWidth: true; spacing: 6
                        Text { text: "匯入 REW 量測，建立你的 Baseline"; color: "#dce8f2"; font.pixelSize: 16; font.weight: Font.DemiBold }
                        Text { text: "支援 .mdat 與 REW 頻響文字檔。確認每筆聲道、位置、Cal 與量測條件後，再選取中央位置 L/R 作為基準。"; color: "#8da4ba"; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap; lineHeight: 1.4 }
                    }
                    RPButton { objectName: "importBaseline"; text: "+  匯入基準量測"; variant: "primary"; enabled: app.hasProject && !s.busy; onClicked: bridge.importFiles("baseline", "") }
                }
            }
            RPCard {
                visible: !(s.measurements || []).length
                Layout.fillWidth: true
                implicitHeight: 340
                EmptyState { anchors.fill: parent; title: "你的第一筆量測，從這裡開始"; subtitle: app.hasProject ? "在 REW 完成量測並保存後，使用上方的匯入按鈕。原始檔會完整保留。" : "先在空間總覽建立專案，再匯入錄製好的 REW 檔案。" }
            }
            RowLayout {
                visible: (s.measurements || []).length > 0
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                spacing: 17
                RPCard {
                    Layout.preferredWidth: 269
                    Layout.minimumWidth: 239
                    Layout.alignment: Qt.AlignTop
                    implicitHeight: selectionColumn.implicitHeight + 34
                    ColumnLayout {
                        id: selectionColumn
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 17
                        spacing: 12
                        RowLayout {
                            Text { text: "全部量測"; color: "#e3edf4"; font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true }
                            Text { text: (s.measurements || []).length + " 筆"; color: "#7f98b0"; font.pixelSize: 11 }
                        }
                        Text { text: "點選查看；勾選納入 Baseline。"; color: "#7892a9"; font.pixelSize: 10 }
                        RowLayout {
                            RPButton { objectName: "selectP0Baselines"; text: "選取 P0 L/R"; compact: true; onClicked: chosenIds = (s.measurements || []).filter(function(m){return page.eligible(m) && m.position === "P0" && (m.channel === "L" || m.channel === "R");}).map(function(m){return m.id;}) }
                            RPButton { text: "清除"; compact: true; variant: "ghost"; onClicked: chosenIds = [] }
                        }
                        Repeater {
                            model: s.measurements || []
                            delegate: Rectangle {
                                id: rowCard
                                required property var modelData
                                Layout.fillWidth: true
                                height: 90
                                radius: 8
                                color: page.selected.id === modelData.id ? "#213a48" : "#101d2c"
                                border.color: page.selected.id === modelData.id ? "#5c9d94" : "#273a4d"
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: bridge.selectMeasurement(rowCard.modelData.id) }
                                RowLayout {
                                    anchors.fill: parent; anchors.margins: 11; spacing: 8
                                    RPCheckBox {
                                        objectName: "baselineCheck_" + rowCard.modelData.id
                                        Layout.preferredWidth: 21
                                        enabled: page.eligible(rowCard.modelData)
                                        checked: page.chosenIds.indexOf(rowCard.modelData.id) >= 0
                                        onClicked: page.toggle(rowCard.modelData.id, checked)
                                    }
                                    ColumnLayout {
                                        Layout.fillWidth: true; spacing: 5
                                        Text { text: rowCard.modelData.name || "未命名量測"; color: "#d7e5ed"; font.pixelSize: 12; font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideMiddle }
                                        Text { text: (rowCard.modelData.channel || "Unknown") + "  ·  " + (rowCard.modelData.position || "P0") + "  ·  " + (rowCard.modelData.role === "verification" ? "補錄" : "基準候選"); color: "#94aabe"; font.pixelSize: 10 }
                                        Text { text: page.calLabel(rowCard.modelData); color: page.calColor(rowCard.modelData); font.pixelSize: 10 }
                                    }
                                }
                            }
                        }
                        Rectangle { Layout.fillWidth: true; height: 1; color: "#2b3e50"; Layout.topMargin: 5 }
                        Text { text: "已選取 " + chosenIds.length + " 筆"; color: "#a7becd"; font.pixelSize: 11 }
                        RPButton { objectName: "setBaseline"; text: "設定為 Baseline"; variant: "primary"; Layout.fillWidth: true; enabled: chosenIds.length > 0 && !s.busy; onClicked: { acknowledge.checked = false; baselineDialog.open(); } }
                        Text { text: "更新基準會建立新版本。需包含 P0 獨立 L/R，可補充其他位置；驗證量測不能當作未校正基準。"; color: "#6f899f"; font.pixelSize: 10; Layout.fillWidth: true; wrapMode: Text.WordWrap; lineHeight: 1.4 }
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 650
                    Layout.alignment: Qt.AlignTop
                    spacing: 17
                    RPCard {
                        Layout.fillWidth: true
                        implicitHeight: curveColumn.implicitHeight + 34
                        ColumnLayout {
                            id: curveColumn
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 17
                            spacing: 13
                            RowLayout {
                                Text { text: selected.name || "選取一筆量測"; Layout.fillWidth: true; elide: Text.ElideRight; color: "#e2edf4"; font.pixelSize: 16; font.weight: Font.DemiBold }
                                RPCheckBox { text: "低頻檢視"; checked: true; onCheckedChanged: measurementChart.lowOnly = checked }
                            }
                            FrequencyChart { id: measurementChart; objectName: "measurementChart"; Layout.fillWidth: true; Layout.preferredHeight: 312; lowOnly: true; curves: (s.chart || {}).curves || []; frequencyMin: (s.chart || {}).f_min || 20; frequencyMax: (s.chart || {}).f_max || 20000 }
                        }
                    }
                    RPCard {
                        visible: !!selected.id
                        Layout.fillWidth: true
                        implicitHeight: classifyColumn.implicitHeight + 36
                        ColumnLayout {
                            id: classifyColumn
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18
                            spacing: 13
                            SectionTitle { title: "確認量測用途"; subtitle: "自動辨識的名稱只是提示；請確認實際播放聲道與麥克風位置。"; Layout.fillWidth: true }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 10
                                ColumnLayout {
                                    Layout.fillWidth: true; Layout.preferredWidth: 1; spacing: 7
                                    Text { text: "播放聲道"; color: "#91a8bd"; font.pixelSize: 11 }
                                    RPComboBox { id: channelChoice; objectName: "measurementChannel"; Layout.fillWidth: true; model: ["Unknown", "L", "R", "LR"]; currentIndex: Math.max(0, model.indexOf(selected.channel || "Unknown")) }
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true; Layout.preferredWidth: 1; spacing: 7
                                    Text { text: "麥克風位置"; color: "#91a8bd"; font.pixelSize: 11 }
                                    RPComboBox { id: positionChoice; objectName: "measurementPosition"; Layout.fillWidth: true; model: ["P0", "P-10", "P+10"]; currentIndex: Math.max(0, model.indexOf(selected.position || "P0")) }
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true; Layout.preferredWidth: 1; spacing: 7
                                    Text { text: "量測用途"; color: "#91a8bd"; font.pixelSize: 11 }
                                    RPComboBox { id: roleChoice; objectName: "measurementRole"; Layout.fillWidth: true; model: ["基準候選", "套用後驗證"]; currentIndex: selected.role === "verification" ? 1 : 0 }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                RPComboBox {
                                    id: peqAssociation
                                    objectName: "measurementPeqAssociation"
                                    visible: roleChoice.currentIndex === 1
                                    Layout.fillWidth: true
                                    model: s.peqs || []; textRole: "name"
                                    currentIndex: { var rows = s.peqs || []; for(var i=0;i<rows.length;i++) if(rows[i].id === selected.applied_peq_id) return i; return rows.length ? 0 : -1; }
                                    displayText: currentIndex >= 0 ? model[currentIndex].name : "先建立並套用 PEQ 方案"
                                }
                                Text { visible: roleChoice.currentIndex === 0; text: "P−10 / P+10：向左 / 右偏移 10 cm"; color: "#708da4"; font.pixelSize: 10; Layout.fillWidth: true }
                                RPButton { objectName: "saveMeasurementClassification"; text: "儲存用途"; compact: true; enabled: !s.busy && (roleChoice.currentIndex === 0 || peqAssociation.currentIndex >= 0); onClicked: bridge.updateMeasurement(selected.id, channelChoice.currentText, positionChoice.currentText, roleChoice.currentIndex === 1 ? "verification" : "baseline", roleChoice.currentIndex === 1 && peqAssociation.currentIndex >= 0 ? peqAssociation.model[peqAssociation.currentIndex].id : "") }
                            }
                        }
                    }
                    RPCard {
                        visible: !!selected.id
                        Layout.fillWidth: true
                        implicitHeight: metadataColumn.implicitHeight + 36
                        ColumnLayout {
                            id: metadataColumn
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18
                            spacing: 13
                            RowLayout {
                                Text { text: "檔案記錄的設定"; color: "#dce8f2"; font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true }
                                Text { text: page.calLabel(selected); color: page.calColor(selected); font.pixelSize: 11 }
                            }
                            Repeater {
                                model: [
                                    {label:"麥克風 Cal", value:meta.cal_name || (meta.cal_status === "missing" ? "未載入" : "檔案未提供名稱")},
                                    {label:"錄音輸入", value:meta.input_device || "未提供"},
                                    {label:"播放輸出", value:meta.output_device || "未提供"},
                                    {label:"取樣率 / 掃頻長度", value:(meta.sample_rate ? app.num(meta.sample_rate / 1000, 1) + " kHz" : "—") + " / " + (meta.sweep_length ? String(meta.sweep_length) + " samples" : "—")},
                                    {label:"掃頻輸出 / Headroom", value:(meta.sweep_level_dbfs !== undefined && meta.sweep_level_dbfs !== null ? app.num(meta.sweep_level_dbfs,1) + " dBFS" : "—") + " / " + (meta.headroom_db !== undefined && meta.headroom_db !== null ? app.num(meta.headroom_db,1) + " dB" : "—")},
                                    {label:"Clipping 紀錄", value:meta.clipping === true ? "有過載紀錄" : meta.clipping === false ? "記錄顯示無過載" : "無法由現有資料確認"},
                                    {label:"來源", value:meta.source_file || selected.source_file || "—"}
                                ]
                                delegate: RowLayout {
                                    required property var modelData
                                    Layout.fillWidth: true; spacing: 12
                                    Text { text: modelData.label; color: "#7d96ad"; font.pixelSize: 11; Layout.preferredWidth: 130 }
                                    Text { text: String(modelData.value); color: "#bdcfdd"; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WrapAnywhere }
                                }
                            }
                        }
                    }
                    RPCard {
                        visible: !!selected.id
                        Layout.fillWidth: true
                        implicitHeight: qualityColumn.implicitHeight + 36
                        ColumnLayout {
                            id: qualityColumn
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18
                            spacing: 13
                            RowLayout {
                                Text { text: "品質與設定檢查"; color: "#dce8f2"; font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true }
                                RPCheckBox { text: "顯示已通過"; checked: page.showAllQuality; onClicked: page.showAllQuality = checked }
                            }
                            Text { text: "只對現有資料能支持的項目下判斷；未提供的資訊會明確標示。"; color: "#7a94aa"; font.pixelSize: 10; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                            Repeater {
                                model: page.qualityRows()
                                delegate: Rectangle {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    implicitHeight: reportRow.implicitHeight + 22
                                    radius: 7; color: "#101e2d"
                                    RowLayout {
                                        id: reportRow
                                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 11; spacing: 12
                                        Text { text: page.levelName(modelData.level); color: page.levelColor(modelData.level); font.pixelSize: 10; Layout.preferredWidth: 56; Layout.alignment: Qt.AlignTop; Layout.topMargin: 2 }
                                        ColumnLayout {
                                            Layout.fillWidth: true; spacing: 5
                                            Text { text: modelData.title || modelData.code || "檢查結果"; color: "#c5d8e4"; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                            Text { text: modelData.detail || ""; color: "#809ab0"; font.pixelSize: 10; Layout.fillWidth: true; wrapMode: Text.WordWrap; lineHeight: 1.4 }
                                        }
                                    }
                                }
                            }
                            Text { visible: page.qualityRows().length === 0; text: "目前沒有需顯示的提醒。可開啟「顯示已通過」查看完整檢查。"; color: "#8ca5ba"; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        }
                    }
                }
            }
        }
    }
    Dialog {
        id: baselineDialog
        objectName: "baselineConfirmDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: 550
        modal: true
        padding: 25
        background: Rectangle { color: "#142335"; radius: 14; border.color: "#3a5063" }
        contentItem: ColumnLayout {
            spacing: 17
            SectionTitle { title: "確認這組 Baseline"; subtitle: "將目前勾選的 " + page.chosenIds.length + " 筆量測保存為新的基準版本。"; Layout.fillWidth: true }
            Text { text: "請查看品質報告，核對 Cal、聲道、位置與播放條件。可以匯入另一台電腦錄製的量測，本機裝置不必相同。設定記錄不完整或出現提醒時，可選擇略過後繼續。"; color: "#a2b9ca"; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap; lineHeight: 1.5 }
            RPCheckBox { id: acknowledge; objectName: "acknowledgeBaselineWarnings"; text: "略過設定與品質提醒，仍使用這組量測。"; Layout.fillWidth: true }
            Text { text: "提醒與略過決定會保留；未知項目不會改為已通過。無效數據、頻段不足或缺少必要量測仍需處理。"; color: "#7994a9"; font.pixelSize: 10; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                RPButton { text: "返回檢查"; onClicked: baselineDialog.close() }
                RPButton { objectName: "confirmBaseline"; text: "建立 Baseline"; variant: "primary"; onClicked: { bridge.setBaseline(JSON.stringify(page.chosenIds), acknowledge.checked); baselineDialog.close(); } }
            }
        }
    }
}
