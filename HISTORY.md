# RoomPilot 原始碼版本紀錄

本 repository 於 2026-09-05 整理，依已保存的原始碼快照建立四個循序 commit 與 annotated tag。這些是快照匯入紀錄，不是當時逐次編輯的原始 Git 歷史；commit 時間採本次匯入時間，沒有回填推估的開發日期。

最初四個標籤保留各版原始碼快照；後續版本使用正常的開發 commit。`main` 為目前已完成驗證的版本。

## 版本對照

| Git tag | 保存來源 | 主要演進 |
|---|---|---|
| `v0.1.0` | `RoomPilot-0.1.0-source.zip` | 內建 MDAT 解析、空間專案與量測流程、第一版 PEQ |
| `v0.2.0` | `RoomPilot-0.2.0-source.zip` | 固定目標參考範圍、Band 分配與頻段延伸、PEQ 圖與左右切換、PEQ 回收區 |
| `v0.3.0` | `RoomPilot-0.3.0-source.zip` | 候選策略比較、可讀決策指標與 Band 移除比較 |
| `v0.4.0` | `RoomPilot-0.4.0-source.zip` | 一個 PEQ 版本內含三策略、策略識別碼、受限制求解器、最多 128 Bands |
| `v0.5.0` | 本 repository 的開發 commit | 逐筆新增缺口／局部與範圍外保護、峰寬深度共同搜尋、完整可行方案保留、曲線合併與共用穩定幅頻計算 |

原始碼版本與使用者專案內的 PEQ v1、v7 等方案編號不同。PEQ 方案編號由各專案自己的操作歷史決定。

## 保存來源核對

建立 Git 歷史前，已逐檔核對四個原始碼快照與對應 source ZIP。v0.1.0、v0.2.0 完全一致；v0.3.0、v0.4.0 的 ZIP 未收錄 `.gitignore`，其餘檔案完全一致。標籤內另外保留當時原始碼資料夾的 `.gitignore`。

| Source ZIP | SHA-256 |
|---|---|
| `RoomPilot-0.1.0-source.zip` | `a3678851d69f0317744b51f1479778bade8b9017279000fc2e1ef8329c4323c2` |
| `RoomPilot-0.2.0-source.zip` | `e393e4be20d7089d6b1009844c345f42decefeaeb838cab9dae6992f7fc68740` |
| `RoomPilot-0.3.0-source.zip` | `70d89d98df4b1a061e6e69b28cc4f1463a5648621d7793a59853d78dbb412da5` |
| `RoomPilot-0.4.0-source.zip` | `7da6e2431938efa8859edd66b0544bf57f163afb6c238af3e62531411bd3d164` |

ZIP 與 Windows 可攜版保留在原本的本機輸出位置，不納入 Git 原始碼歷史。Repository 保存程式碼、測試、模型說明、驗證紀錄與第三方授權文件；實際量測檔、專案資料庫、個人 PEQ 匯出與研究數據不在本次匯入範圍。需要私有量測的整合測試，依 README 透過本機檔案執行。

初始四個快照沒有修改當時的 PEQ 演算法。Q 值研究的工程改善從 v0.5.0 開始納入；仍未包含新量測的聲學或受控聆聽驗證。各版模型及驗收限制見對應標籤的文件。

## 查看版本差異

```powershell
git log --oneline --decorate
git diff v0.1.0 v0.2.0 --stat
git diff v0.3.0 v0.4.0 -- roompilot/analysis.py roompilot/solver.py
git show v0.3.0:README.md
```

各標籤是當時保存的版本，並不表示所有功能均完成實測；支援範圍與限制以該標籤的 `KNOWN_LIMITATIONS.md`、`VALIDATION.md` 與 README 為準。
