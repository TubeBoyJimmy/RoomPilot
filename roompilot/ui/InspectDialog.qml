import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: control
    property string heading: "紀錄詳情"
    property string subheading: ""
    property string bodyText: ""
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(730, parent ? parent.width - 60 : 730)
    height: Math.min(650, parent ? parent.height - 60 : 650)
    modal: true
    padding: 24
    background: Rectangle { color: "#142335"; radius: 14; border.color: "#3a5063" }
    contentItem: ColumnLayout {
        spacing: 16
        SectionTitle { title: control.heading; subtitle: control.subheading; Layout.fillWidth: true }
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            TextArea {
                text: control.bodyText
                color: "#b6ccdc"
                font.pixelSize: 12
                wrapMode: TextEdit.Wrap
                readOnly: true
                selectByMouse: true
                padding: 15
                selectionColor: "#3e7d74"
                selectedTextColor: "white"
                background: Rectangle { color: "#0e1c2c"; radius: 8; border.color: "#2c4053" }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Text { text: "可選取文字並複製"; color: "#7793a9"; font.pixelSize: 10; Layout.fillWidth: true }
            RPButton { objectName: "closeInspection"; text: "完成"; variant: "primary"; onClicked: control.close() }
        }
    }
}
