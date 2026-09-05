import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window
    objectName: "roomPilotWindow"
    width: 1360
    height: 920
    minimumWidth: 1100
    minimumHeight: 760
    visible: true
    title: "RoomPilot · 空間校正工作室"
    color: "#0c1725"
    font.family: "Microsoft JhengHei UI"
    font.pixelSize: 13
    property var s: bridge.state || ({})
    property var project: s.project || ({})
    property var selectedPeq: s.selected_peq || ({})
    property bool hasProject: !!project.id
    property var pageTitles: ["空間總覽", "量測資料", "PEQ 工作室", "專案紀錄"]
    property var pageSubtitles: ["從可靠量測開始，循序改善你的聆聽空間。", "保留每一筆量測，讓校正有據可循。", "以最少的濾波器，處理最值得修正的問題。", "每次調整都有脈絡，隨時找回已驗證的方案。"]
    function safe(value, fallback) { return value === undefined || value === null || value === "" ? fallback : value; }
    function num(value, digits) { return value === undefined || value === null || !isFinite(Number(value)) ? "—" : Number(value).toFixed(digits === undefined ? 1 : digits); }
    function formatTime(value) { if (!value) return ""; var date = new Date(value); return isNaN(date.getTime()) ? String(value) : Qt.formatDateTime(date, "yyyy/MM/dd  HH:mm"); }
    function openNewProject() { newName.text = ""; newNotes.text = ""; newProjectDialog.open(); newName.forceActiveFocus(); }
    function go(page) { bridge.setPage(page); }
    function statusLabel(status) { return ({suggested:"待套用", draft:"待套用", applied:"已套用", verified:"已驗證", edited:"已修改", generated:"待套用"})[status] || status || "待套用"; }

    RowLayout {
        anchors.fill: parent
        spacing: 0
        Rectangle {
            Layout.preferredWidth: 210
            Layout.fillHeight: true
            color: "#101e2e"
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: "#263447" }
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 19
                spacing: 9
                RowLayout {
                    Layout.topMargin: 13
                    Layout.bottomMargin: 25
                    spacing: 11
                    Rectangle {
                        width: 35; height: 35; radius: 10; color: "#83dbc5"
                        Text { anchors.centerIn: parent; text: "∿"; font.family: "Segoe UI Symbol"; font.pixelSize: 29; color: "#10352f"; font.bold: true }
                    }
                    ColumnLayout {
                        spacing: 1
                        Text { text: "RoomPilot"; color: "#eff5fa"; font.pixelSize: 21; font.weight: Font.DemiBold; font.family: "Segoe UI" }
                        Text { text: "空間校正工作室"; color: "#8197ac"; font.pixelSize: 10 }
                    }
                }
                Text { text: "目前專案"; color: "#6e859c"; font.pixelSize: 10; font.letterSpacing: 1; Layout.bottomMargin: 2 }
                RPComboBox {
                    id: projectSelector
                    objectName: "projectSelector"
                    Layout.fillWidth: true
                    model: window.s.projects || []
                    textRole: "name"
                    displayText: window.hasProject ? window.project.name : "尚未建立專案"
                    onActivated: function(index) { bridge.selectProject(model[index].id); }
                }
                RPButton { objectName: "sidebarNewProject"; text: "+  新建專案"; variant: "ghost"; Layout.fillWidth: true; onClicked: window.openNewProject() }
                Rectangle { Layout.fillWidth: true; height: 1; color: "#2a394b"; Layout.topMargin: 12; Layout.bottomMargin: 15 }
                Repeater {
                    model: [ {title:"空間總覽", icon:"⌂", number:"01"}, {title:"量測資料", icon:"∿", number:"02"}, {title:"PEQ 工作室", icon:"≋", number:"03"}, {title:"專案紀錄", icon:"↺", number:"04"} ]
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        objectName: "nav" + index
                        Layout.fillWidth: true
                        height: 49
                        radius: 8
                        color: (window.s.page || 0) === index ? "#223b47" : navMouse.containsMouse ? "#192c3e" : "transparent"
                        Rectangle { width: 3; height: 18; radius: 2; anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; color: "#83dbc5"; visible: (window.s.page || 0) === index }
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 13; anchors.rightMargin: 12; spacing: 10
                            Text { text: modelData.icon; font.family: "Segoe UI Symbol"; font.pixelSize: 23; color: (window.s.page || 0) === index ? "#92e2cd" : "#7c93a8"; Layout.preferredWidth: 24 }
                            Text { text: modelData.title; color: (window.s.page || 0) === index ? "#daf3ed" : "#a0b3c5"; font.pixelSize: 13; font.weight: Font.DemiBold; Layout.fillWidth: true }
                            Text { text: modelData.number; color: "#547287"; font.pixelSize: 9; font.family: "Segoe UI" }
                        }
                        MouseArea { id: navMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: window.go(index) }
                    }
                }
                Item { Layout.fillHeight: true }
                RPCard {
                    Layout.fillWidth: true
                    implicitHeight: 108
                    color: "#162a39"
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 13; spacing: 5
                        Text { text: "下一步"; font.pixelSize: 10; color: "#83dbc5" }
                        Text { text: !window.hasProject ? "建立你的第一個空間" : !window.s.baseline_ready ? "完成量測並確認 Baseline" : !window.selectedPeq.id ? "產生第一版 PEQ 建議" : "套用後，在相同位置補錄"; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 12; lineHeight: 1.3; color: "#c0d1df" }
                    }
                }
                RPButton { text: "REW 官方網站  ↗"; variant: "ghost"; compact: true; Layout.fillWidth: true; Layout.topMargin: 6; onClicked: bridge.openLink("rew") }
                Text { text: "LOCAL WORKSPACE  ·  v0.4"; font.family: "Segoe UI"; color: "#536c81"; font.pixelSize: 9; Layout.alignment: Qt.AlignHCenter; Layout.bottomMargin: 3 }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 111
                color: "#0c1725"
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 30; anchors.rightMargin: 30; anchors.topMargin: 19; anchors.bottomMargin: 17
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 7
                        RowLayout {
                            spacing: 10
                            Text { text: window.pageTitles[window.s.page || 0]; color: "#eff5fa"; font.pixelSize: 27; font.weight: Font.DemiBold }
                            Rectangle {
                                visible: window.s.baseline_ready === true
                                implicitWidth: baselineTag.implicitWidth + 16; height: 24; radius: 6; color: "#1b3938"
                                Text { id: baselineTag; anchors.centerIn: parent; text: "Baseline v" + (window.project.baseline_version || 1); color: "#83dbc5"; font.pixelSize: 10 }
                            }
                        }
                        Text { text: window.pageSubtitles[window.s.page || 0]; color: "#889fb4"; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight }
                    }
                    RPButton { objectName: "importBundleTop"; visible: !window.hasProject; text: "開啟專案包"; onClicked: bridge.importProject() }
                    RPButton { objectName: "importMeasurementTop"; visible: window.hasProject; enabled: !window.s.busy; text: "+  匯入量測"; variant: "primary"; onClicked: bridge.importFiles("baseline", "") }
                }
            }
            Rectangle {
                visible: !!window.s.message
                Layout.fillWidth: true
                Layout.leftMargin: 30; Layout.rightMargin: 30; Layout.bottomMargin: 14
                implicitHeight: Math.max(46, messageText.implicitHeight + 24)
                radius: 8
                color: window.s.message_kind === "error" ? "#392a32" : window.s.message_kind === "warning" ? "#373226" : "#1b333b"
                border.color: window.s.message_kind === "error" ? "#794a52" : window.s.message_kind === "warning" ? "#6c5e39" : "#335662"
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 8; spacing: 12
                    Text { text: window.s.message_kind === "error" || window.s.message_kind === "warning" ? "!" : "✓"; color: window.s.message_kind === "error" ? "#ffb4a7" : "#83dbc5"; font.family: "Segoe UI Symbol"; font.pixelSize: 17; font.bold: true }
                    Text { id: messageText; text: window.s.message || ""; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#d3e0eb"; font.pixelSize: 12; lineHeight: 1.3 }
                    RPButton { text: "×"; implicitWidth: 30; compact: true; variant: "ghost"; onClicked: bridge.clearMessage() }
                }
            }
            Rectangle {
                visible: !!window.s.busy
                Layout.fillWidth: true; Layout.leftMargin: 30; Layout.rightMargin: 30; Layout.bottomMargin: 14
                height: 48; color: "#162839"; radius: 8
                RowLayout {
                    anchors.fill: parent; anchors.margins: 10; spacing: 14
                    Text { text: "處理中"; color: "#83dbc5"; font.pixelSize: 12 }
                    ProgressBar { Layout.fillWidth: true; value: window.s.progress || 0; indeterminate: !window.s.progress; background: Rectangle { implicitHeight: 4; radius: 2; color: "#2c4052" } contentItem: Item { implicitHeight: 4; Rectangle { width: parent.width * parent.parent.visualPosition; height: parent.height; radius: 2; color: "#83dbc5" } } }
                    RPButton { objectName: "cancelWork"; text: "取消"; compact: true; variant: "ghost"; onClicked: bridge.cancelWork() }
                }
            }
            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: window.s.page || 0
                OverviewPage { app: window }
                MeasurementsPage { app: window }
                PeqPage { app: window }
                HistoryPage { app: window }
            }
            Rectangle {
                Layout.fillWidth: true; height: 30; color: "#0c1725"
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 30; anchors.rightMargin: 30
                    Rectangle { width: 5; height: 5; radius: 3; color: window.hasProject ? "#83dbc5" : "#63788c" }
                    Text { text: window.hasProject ? "專案自動儲存於本機" : "資料保存在你的電腦"; color: "#6e879d"; font.pixelSize: 10 }
                    Item { Layout.fillWidth: true }
                    Text { text: "量測 → 校正 → 驗證"; color: "#6e879d"; font.pixelSize: 10 }
                }
            }
        }
    }
    Dialog {
        id: newProjectDialog
        objectName: "newProjectDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: 500
        modal: true
        padding: 25
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { radius: 14; color: "#142335"; border.color: "#3a5063" }
        contentItem: ColumnLayout {
            spacing: 16
            SectionTitle { title: "建立一個聆聽空間"; subtitle: "量測、PEQ 與驗證紀錄，都會保存在這個專案中。" }
            Text { text: "專案名稱"; color: "#aebfd0"; font.pixelSize: 12; Layout.topMargin: 8 }
            RPField { id: newName; objectName: "newProjectName"; Layout.fillWidth: true; placeholderText: "例如：書房 · 桌面近場"; maximumLength: 100; onAccepted: { if (text.trim()) { bridge.createProject(text.trim(), newNotes.text); newProjectDialog.close(); } } }
            Text { text: "空間與系統備註（選填）"; color: "#aebfd0"; font.pixelSize: 12 }
            TextArea {
                id: newNotes
                objectName: "newProjectNotes"
                Layout.fillWidth: true; Layout.preferredHeight: 96
                color: "#e5edf5"; font.pixelSize: 13; wrapMode: TextEdit.Wrap; selectByMouse: true; padding: 12
                placeholderText: "喇叭、房間、聆聽位置，或想改善的地方…"; placeholderTextColor: "#6f8297"
                background: Rectangle { color: "#0e1a29"; radius: 7; border.color: newNotes.activeFocus ? "#83dbc5" : "#304157" }
            }
            RowLayout {
                Layout.fillWidth: true; Layout.topMargin: 8
                Item { Layout.fillWidth: true }
                RPButton { text: "取消"; onClicked: newProjectDialog.close() }
                RPButton { objectName: "createProjectConfirm"; text: "建立專案"; variant: "primary"; enabled: newName.text.trim().length > 0 && !window.s.busy; onClicked: { bridge.createProject(newName.text.trim(), newNotes.text); newProjectDialog.close(); } }
            }
        }
    }
}
