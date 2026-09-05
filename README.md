# RoomPilot · 空間校正工作室

RoomPilot 是在 Windows 本機使用的 REW 量測與 PEQ 專案工具。建立空間、直接匯入 `.mdat`、確認 Mic Cal 與量測品質，再產生可手動套用的 PEQ 建議，最後用補錄驗證結果。

**一般 `.mdat V2` 匯入使用內建解析器，不需要開啟或安裝 REW，也不會在背景啟動 REW。** REW 用來錄製量測；RoomPilot 負責讀取、分析與保存專案。v0.2 的支援範圍與驗證限制請見 [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md)。

## v0.2 的主要變更

- 自動目標參考固定為 P0 的 80–200 Hz，與校正範圍分開；可在進階欄位修改參考或手動指定目標。這是透明的起始政策，不是每個空間的最佳目標。
- 預設「低頻優先，餘額擴充」與深入搜尋。擴大到 500 Hz 時，先處理低頻，再用剩餘 Band 搜尋新增修正。
- 要延續指定版本，先選取它，再選「保留原案，擴充高頻」。原目標與濾波器鎖定，新增中心在舊範圍以上，並限制原範圍的合成曲線變化為 0.5 dB。此上限是本程式政策。能力限制不相容時要求修改設定，Band 已用完時保留原案，不靜默重排。
- 「整段重新最佳化」明確允許重新分配所有 Band。新增 Band 必須有足夠收益；局部波峰未超出目標或限制不允許時，可以保持空白。
- SPL 與 PEQ Gain 圖分開，可各自切換 L／R／左右；PEQ 圖可顯示各 Band。已保存方案可查看目標、演算法版本、搜尋工作量與分配依據。
- 「刪除 PEQ」移至專案紀錄的可還原回收區，保留補錄與套用歷史；不會改變設備上的 PEQ。完全相同的重算结果不另存重複版本。
- 來源裝置不必存在於分析電腦。Cal、參數、Clipping 等提醒可確認後繼續，提醒本身不會變成通過；無效数值、缺少必要聲道或沒有可用頻段仍須修正。補錄條件差異可確認後作參考比較，會標為未驗證。

v0.1 的舊方案不會自動改寫；新計算採用 `roompilot-peq-2.0`。手動調整舊方案會按新版驗算並另存，請查看新版本的限制與理由。低頻參考與中頻局部趨勢不同時，單一平坦目標仍有局限；不能為了新增一段就無意間降低整個目標。

## 啟動

### Windows 可攜版

若使用附帶的 Windows 可攜套件，先完整解壓縮，再執行套件內的 `RoomPilot.exe`。請保留旁邊的相依檔案，不要只搬移 `.exe`。可攜版不需要另行安裝 Python；專案資料預設存放在目前 Windows 帳號的本機應用程式資料夾。

### 從原始碼執行

本版使用 Python、PySide6／Qt Quick、NumPy、SciPy 與 javaobj-py3。專案宣告 Python 3.11 以上；開發驗證環境為 Windows、Python 3.13。請在含有 `pyproject.toml` 的 RoomPilot 資料夾開啟 PowerShell，依序執行：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m roompilot
```

首次安裝相依套件需要網路。完成安裝後，量測匯入、分析與專案保存都在本機進行。程式內的 REW 說明連結會使用瀏覽器開啟官方網站。

## 第一次使用：完成一個校正循環

### 1. 建立空間並記錄條件

在「空間總覽」建立專案。選擇系統列出的音訊輸入／輸出裝置，並記錄麥克風、主音量、喇叭與聆聽位置、DSP 播放路徑等資訊。

**Audio Device Name 只是裝置識別資訊。** 選擇某台 DAC 並不代表 PEQ 一定套用在 DAC；你仍可能使用系統 DSP 或 Roon 等播放器。PEQ 套用位置可以另外填寫，功能限制也要依實際使用的 DSP 確認。

依環境清單完成準備：

- 暫時關閉冷氣、除濕機與風扇，避免氣流吹向麥克風。
- 暫停其他音樂、通知、談話與走動，選擇背景噪音穩定的時間。
- 用腳架固定麥克風，在中央聆聽位置標記 **P0**，記錄耳高、朝向與位置。
- 門窗、窗簾、家具維持選定的聆聽狀態，後續補錄沿用。
- 記錄 EQ、Loudness、音調控制、音量與系統音效狀態。建立未校正 Baseline 時，確認本次要評估的空間 PEQ 尚未啟用。
- 分別檢查左右喇叭：指定 L 時只有左側播放，指定 R 時只有右側播放。

### 2. 在 REW 設定並錄製

從 [REW 官方網站](https://www.roomeqwizard.com/#downloads) 下載 REW。介面位置可能隨版本不同，詳細操作以 [REW 量測說明](https://www.roomeqwizard.com/help/help/html/makingmeasurements.html) 為準。

1. 在 REW 選定麥克風輸入與實際播放輸出，確認聲道配置。
2. 載入該麥克風序號的 Cal 檔，核對 0°／90° 與實際朝向。開始前再查看 Measure 視窗的 **Calibration files**；切換輸入後尤其要重新確認。校正檔設定可參考 [REW Cal Files](https://www.roomeqwizard.com/help/help/html/calfiles.html)。
3. **確認絕對聲壓校正。** USB 麥克風的 Cal 含 `Sens Factor`，而且 REW 支援該裝置的輸入增益關係時，REW 可依靈敏度自動計算 SPL 校正；僅有頻響曲線或檔名並不足夠。沒有可用的靈敏度資料時，開啟 **SPL Meter → Calibrate**，依提示選擇校正訊號，再輸入外部聲壓計或聲壓校正器提供的可靠參考值，按 **Finished** 完成；不要輸入 REW 自己顯示的讀值。沒有可靠參考就只將讀值視為相對電平，不能假定為正確的 dB SPL。切換輸入路徑或調整輸入增益後，重新確認校正是否仍有效。詳見 [REW SPL 校正步驟](https://www.roomeqwizard.com/help/help_en-GB/html/inputcal.html) 與 [Cal 靈敏度說明](https://www.roomeqwizard.com/help/help_en-GB/html/calfiles.html)。
4. 選擇 **SPL** 量測。完整診斷可考慮 20 Hz–20 kHz；若喇叭不適合此範圍，依它的有效與安全頻段調整。量測頻段與稍後的 PEQ 校正頻段是不同設定。
5. 可由裝置支援的 48 kHz、128k 或 256k 掃頻開始。這些是起始選項，並非所有其他設定都不正確。
6. 先降低主音量，執行 **Check Levels**，確認輸入與播放電平合適。`dBFS` 是數位輸出設定，`dB SPL` 是現場聲壓；同一個 dBFS 在不同系統不會得到相同 SPL。頻響 Cal 與絕對聲壓校正也要分開確認。
7. 每次錄製使用單次掃頻，分開保存 A、B 兩筆。依序錄 `L_P0_A`、`L_P0_B`、`R_P0_A`、`R_P0_B`。兩筆之間保持位置與音量不動。可追加 `LR_P0`，但 L+R 不能取代獨立 L／R。
8. 用 **Save all measurements** 保存 `.mdat`，再回到 RoomPilot 匯入。

若 PEQ 只在播放器內生效，REW 直接播放可能繞過它。此時需依 [REW 檔案播放量測說明](https://www.roomeqwizard.com/help/help/html/makingmeasurements.html#fileplayback)，產生包含適當 timing reference 的測試檔，交給實際播放器播放並由 REW 接收。校正前後保持相同路徑，只變更本次 PEQ 狀態。

### 3. 匯入、檢查並保存 Baseline

在「量測資料」匯入 `.mdat`，也可使用 REW 匯出的單筆頻響 `.txt`、`.csv`、`.tsv`。程式會複製保存來源檔，不會覆寫原始量測。

逐筆確認播放聲道、P0／P−10／P+10 位置與量測用途；自動辨識只是建議。檢查曲線、量測參數、Cal 及品質報告。

| Mic Cal 顯示 | 代表的資訊 | 建議處理 |
|---|---|---|
| 已載入 | 此筆 MDAT 保存有效 Mic/Meter Cal 曲線；文字檔則依明確標頭紀錄判斷 | 核對序號、朝向與各筆是否一致 |
| 未載入 | 此筆明確沒有麥克風 Cal 曲線 | 回到 REW 確認、補正並另存後重新匯入 |
| 未確認 | 來源缺少欄位、曲線不完整或資料無法解析 | 查看原量測，避免當成已通過 |

**Mic/Meter Cal 與 Soundcard Cal 不同。** 例如 USB 麥克風沒有音效卡 Cal，不能直接視為漏載麥克風 Cal。Cal 顯示的是量測目前保存的狀態；它也可能在錄製後於 REW 補上或更換。

Cal 正常不等於沒有 clipping。沒有可用的過載紀錄、headroom 或其他條件資料時，程式會保留「資料不足」。左右曲線不同也不一定是錄錯，需配合寬頻音量與 A/B 重現性判斷。

勾選要使用的基準量測並設定 Baseline。至少需包含 P0 的獨立 L、R；重大資料錯誤需先處理。確認品質提醒可以繼續使用有已知限制的資料，但不會把未知項目改成通過。每次更新 Baseline 都新增快照版本。

### 4. 填寫 PEQ 能力並產生建議

在「PEQ 工作室」確認：

| 設定 | 如何選擇 |
|---|---|
| Band 數 | 本次可分配的數量，範圍 1–20；不必用滿 |
| Peak／Bell | DSP 必須能設定頻率 Hz、Gain dB 與 Q；固定頻點圖示 EQ 不能直接套用 |
| 左右各自設定 | Band 數指每個聲道可用數量，輸出 L 與 R 兩組 |
| 左右共用一組 | 產生同一組同時套用 L／R 的濾波器，計算時共同評估兩側 |
| 進階限制 | 核對頻率、Gain、Q 範圍與輸入步進，以及實際 DSP 運算取樣率 |

預設從 **30–200 Hz、只減益**開始，單段減益上限 6 dB、疊加總減益上限 9 dB。軟體優先處理可修正的凸峰，不要求填滿 Band，也不把深窄凹洞硬補平。可依喇叭有效低頻下限調整範圍。

「快速預覽」與預設的「深入搜尋」都使用 CPU、float64 數值分析。深入模式投入更多搜尋與聯合精修，但不保證產生更好的可聽結果；可信的量測、合理的目標與補錄驗證更重要。本版沒有 GPU 計算路徑。

預測圖是根據濾波器計算的幅頻變化，尚不是實測改善。查看每段參數、理由與提醒；進階增益或擴大校正範圍應有足夠的多位置資料支持。

### 5. 套用、補錄並比較

1. 匯出文字或 CSV 參數表，在硬體或軟體 DSP 手動輸入。這是通用參數表，不是原廠設備的專用設定檔。
2. 用**這一版完整設定取代上一版**，停用未使用 Band，核對左右、Peak、Hz、Gain、Q 與 Preamp。不要把新方案疊加在舊方案上。
3. 實際完成後，才按「我已在 DSP 套用此版本」。匯出本身不代表已套用；確認狀態也不會直接控制設備。
4. 在相同 P0、音量、Cal 與播放路徑重錄 L／R，從該 PEQ 版本匯入補錄並執行比較。
5. 再向左、右各移動麥克風 **10 cm**，各位置仍錄獨立 L／R，匯入後標為 P−10、P+10。若沒有這些位置的校正前資料，只能評估目前一致性，不能宣稱該位置已改善。

比較會檢查可比條件，並將整體音量變化與曲線形狀變化分開。差異未超過重錄門檻時，應保留現況或先確認量測。手動修改濾波器會另存新版本並重算預測，舊版本與補錄紀錄保留；新版本需重新套用與驗證。

## 專案保存與搬移

四個頁面分別用於「空間總覽」「量測資料」「PEQ 工作室」「專案紀錄」。專案保存原始檔、量測條件、完整數值曲線、Baseline 快照、PEQ 版本、套用確認與驗證歷史。

Windows 預設資料位置：

```text
%LOCALAPPDATA%\RoomPilot\
  projects.sqlite3
  projects\<專案 ID>\sources\...
  roompilot.log
```

資料庫使用 SQLite WAL；手動備份整個資料夾前，請先關閉程式。跨電腦搬移建議使用「匯出專案包」產生 `.roompilot`，另一台電腦再匯入。匯入會建立獨立副本，保留版本內容，不會覆蓋現有專案。專案包含原始量測與當時記錄的裝置／路徑資訊，分享前請確認內容適合提供給對方。

可使用 `--data-dir` 指定獨立資料夾；未指定時也支援 `ROOMPILOT_DATA_DIR` 環境變數：

```powershell
.\.venv\Scripts\python.exe -m roompilot --data-dir "D:\RoomPilotData"
```

## 開發與驗證

在專案資料夾執行：

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest
```

重現本次環境與打包 Windows 可攜版：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe build_windows.py
```

執行檔位於 `dist\RoomPilot\RoomPilot.exe`；請一起保留 `_internal` 與 `licenses`。打包腳本會隔離開發工具的 PATH，避免夾帶不相容的系統 DLL。可用 `--work-dir` 與 `--dist-dir` 自訂產物位置，`--console` 產生診斷版本。

主要模組：`importers.py` 負責被動讀檔；`analysis.py` 負責品質、PEQ 與補錄比較；`storage.py` 保存本地專案並驗證封存內容；`controller.py` 連接工作佇列與 QML 介面。量測分析使用完整保存資料，圖表另做顯示取樣，不會覆寫來源曲線。

使用者提供的測試 `.mdat` 已成功讀出五筆量測，每筆 54,533 個頻率點，並逐筆取得 `8112838.txt` 的 615 點 Mic Cal。測試核對來源 SHA-256 與修改時間保持不變。該私有量測檔與專案資料**不隨發行版提供**；相關整合測試在沒有這份檔案時會跳過，也可透過 `ROOMPILOT_TEST_MDAT` 指定同一份測試資料的位置。

目前頻率軸與 SPL／Cal 語意已透過已安裝 REW 程式碼的靜態檢查核對，五筆 Cal 顯示也在 REW 官方 GUI 確認。**尚未完成官方文字匯出與內建解析數值的逐點交叉驗證**，請保留這項驗收限制。
