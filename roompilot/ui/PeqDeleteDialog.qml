import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    property string peqId: ""
    property string peqName: ""
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 480
    modal: true
    padding: 25
    background: Rectangle { color: "#142335"; radius: 14; border.color: "#3a5063" }
    contentItem: ColumnLayout {
        spacing: 17
        SectionTitle { title: "移除 " + (dialog.peqName || "這個 PEQ") + "？"; subtitle: "可隨時在「專案紀錄 → 已移除的 PEQ」復原。"; Layout.fillWidth: true }
        Text { text: "此版本及其中所有策略將一起從清單隱藏，參數與各策略既有補錄關聯會保留。這項操作不會更改設備中已套用的 PEQ。"; color: "#a7bfd0"; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap; lineHeight: 1.5 }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            RPButton { text: "保留"; onClicked: dialog.close() }
            RPButton { objectName: dialog.objectName + "Confirm"; text: "移除方案"; variant: "danger"; onClicked: { bridge.deletePeq(dialog.peqId); dialog.close(); } }
        }
    }
}
