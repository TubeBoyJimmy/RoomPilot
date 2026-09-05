import QtQuick
import QtQuick.Controls

ComboBox {
    id: control
    implicitHeight: 40
    implicitWidth: 190
    font.pixelSize: 13
    leftPadding: 12
    rightPadding: 32
    contentItem: Text {
        text: control.displayText
        color: control.enabled ? "#dce6f1" : "#6f8297"
        font: control.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    indicator: Text {
        x: control.width - 25
        y: (control.height - height) / 2
        text: "⌄"
        font.family: "Segoe UI Symbol"
        color: "#92a7bb"
        font.pixelSize: 17
    }
    background: Rectangle {
        radius: 7
        color: "#0e1a29"
        border.color: control.activeFocus ? "#83dbc5" : "#304157"
    }
    delegate: ItemDelegate {
        required property var modelData
        required property int index
        width: control.width
        height: 38
        highlighted: control.highlightedIndex === index
        contentItem: Text {
            text: control.textRole ? modelData[control.textRole] : modelData
            color: "#dce6f1"
            font.pixelSize: 13
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            color: highlighted ? "#2b4356" : "#142335"
        }
    }
    popup: Popup {
        y: control.height + 5
        width: control.width
        implicitHeight: Math.min(contentItem.implicitHeight + 12, 280)
        padding: 6
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator {}
        }
        background: Rectangle { color: "#142335"; radius: 8; border.color: "#3b4b5f" }
    }
}
