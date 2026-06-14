# VMware 監控 Stack (vcsim → vmware_exporter → Prometheus → Grafana)

一套 Docker Compose 環境，用模擬 vCenter (vcsim) 練習 VMware 監控完整鏈路，附三個現成的 Grafana dashboard。未來要對接真實的 vCenter / ESXi 只需要改 docker-compose 的環境變數。

## 架構

```
┌─────────┐  vSphere API  ┌──────────────────┐   /metrics   ┌────────────┐   PromQL    ┌─────────┐
│  vcsim  │ ◄──────────── │ vmware_exporter  │ ◄─────────── │ Prometheus │ ◄────────── │ Grafana │
│ :443    │   (pyVmomi)   │ :9272            │   每 60 秒    │ :9090      │             │ :3000   │
└─────────┘               └──────────────────┘              └────────────┘             └─────────┘
   模擬 vCenter             轉成 Prometheus                     存時序資料                  視覺化
   DC0 / 2 Clusters /       格式
   2 Hosts / 4 VMs /
   1 Datastore
```

## 快速開始

```bash
# 1. 產生三個 dashboard JSON
python3 convert_dashboard.py
python3 modernize_dashboard.py vmware-template-overview.json grafana/dashboards/vmware-overview-prom.json --uid vmware-overview-prom --title "VMware Overview (Prometheus)"
python3 modernize_dashboard.py vmware-template-stats.json    grafana/dashboards/vmware-stats-prom.json    --uid vmware-stats-prom    --title "VMware Stats (Prometheus)"
python3 patch_dashboards.py

# 2. 啟動全部服務
docker compose up -d

# 3. 確認狀態
docker compose ps

# 看 log（debug 用）
docker compose logs -f vmware_exporter
```

啟動後打開瀏覽器：

| 服務 | URL | 帳密 |
|---|---|---|
| Grafana | http://localhost:3000 | admin / admin |
| Prometheus | http://localhost:9090 | — |
| vmware_exporter 原始 metrics | http://localhost:9272/metrics?vsphere_host=vcsim | — |
| vcsim SDK | https://localhost:8989/sdk | user / pass |

Grafana 登入後左側 **Dashboards** 會看到三個自動 provisioned 的 dashboard。

## 三個 Dashboard

| UID | 標題 | 來源 | 用途 |
|---|---|---|---|
| `vmware-vsphere-prom` | VMware vSphere - Overview (Prometheus) | InfluxDB 版 template 8159 經 query 轉換 | 完整版：DC / Cluster / Host / VM / Datastore 全鏈路指標 (22 panels) |
| `vmware-overview-prom` | VMware Overview (Prometheus) | Grafana.com dashboard 18017 | High-level + Host stats + VM stats，含 datastore bargauge |
| `vmware-stats-prom` | VMware Stats (Prometheus) | Grafana.com dashboard 11243 | 較舊但精簡，singlestat → stat 過後 |

點任一 dashboard 進去都該看到 4 個 VM、2 個 host、1 個 datastore 的資料。

## 檔案結構

```
vmware-defense/
├── docker-compose.yml                    ← 四個服務的編排
├── prometheus/
│   └── prometheus.yml                    ← Prometheus scrape 設定
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/datasource.yml    ← 自動接 Prometheus
│   │   └── dashboards/dashboards.yml     ← Dashboard 來源宣告
│   └── dashboards/
│       ├── vmware-vsphere-prom.json      ← 轉好的 dashboard (auto-loaded)
│       ├── vmware-overview-prom.json
│       └── vmware-stats-prom.json
│
├── convert_dashboard.py                  ← InfluxDB/Flux → Prometheus/PromQL 轉換器
├── modernize_dashboard.py                ← 舊版 Prometheus template panel 升級工具
│
├── vmware-template-influxdb.json         ← 原始 template (Telegraf + InfluxDB v2)
├── vmware-template-overview.json         ← 原始 template (gnetId 18017，舊 panel types)
└── vmware-template-stats.json            ← 原始 template (gnetId 11243，含 singlestat)
```

## 兩個轉換腳本

### convert_dashboard.py

把 **InfluxDB/Flux 查詢的 dashboard** 轉成 **Prometheus/PromQL** 版本（query 整個重寫）。

```bash
# 不帶參數時，預設使用同層目錄下的 vmware-template-influxdb.json
# 輸出到 grafana/dashboards/vmware-vsphere-prom.json
python3 convert_dashboard.py

# 也可指定來源與目的
python3 convert_dashboard.py path/to/src.json path/to/dst.json
```

腳本內部有兩個重點對應表：

- `METRIC_MAP` — `(Telegraf measurement, field)` → PromQL 表達式
- `TITLE_OVERRIDES` — 太複雜（含 `pivot`/`join`/`map`）無法 1:1 轉換的 panel，用手寫的 PromQL 覆蓋

要新增對應或調整公式直接改這兩個字典再重跑即可。

### modernize_dashboard.py

把**已經是 PromQL 但用舊 panel 類型**的 dashboard 升級到 Grafana 13 可用（Angular plugin 在 Grafana 12 以後完全移除）。

```bash
python3 modernize_dashboard.py <src.json> <dst.json> --uid <new-uid> --title "<New Title>"
```

主要做：
- 刪掉 `__inputs` / `__requires` / `__elements`（import wizard 用，provisioning 不需要）
- 把 datasource UID 變數（`${DS_PROMETHEUS}` 等）換成實際的 `PBFA97CFB590B2093`
- Panel type 升級：`graph` → `timeseries`、`singlestat` → `stat`、`table-old` → `table`
- 移除 Angular 時代的 deprecated 欄位
- 盡可能保留 Y 軸 unit (percent / ms / kbytes …)

## 對接真實 vCenter / ESXi

未來要換掉 vcsim 改抓真環境，三步驟：

1. 把 `docker-compose.yml` 裡 `vcsim` service 整段刪掉（或留著當測試）
2. 改 `vmware_exporter` 的環境變數：
   ```yaml
   VSPHERE_HOST: vcenter.your-company.com
   VSPHERE_USER: monitoring@vsphere.local
   VSPHERE_PASSWORD: <password>
   VSPHERE_IGNORE_SSL: "FALSE"  # 如果 vCenter 有正確憑證
   ```
3. 改 `prometheus/prometheus.yml` 裡 `vsphere_host: ['vcsim']` 改成同樣的 host

其他 Grafana datasource、dashboard 都不用動，重啟即可。

## 故障排除

| 問題 | 怎麼查 |
|---|---|
| Dashboard panel 顯示 "No Data" | Prometheus → http://localhost:9090/graph 直接執行 panel 的 query 看是否有資料 |
| Exporter 沒抓到 | `docker compose logs -f vmware_exporter`，看是否能連到 vcsim |
| vcsim 連不到 | `docker compose logs vcsim`，預設 listen `0.0.0.0:443` |
| Grafana datasource 紅燈 | Configuration → Data sources 進去 Save & test |
| 改了 dashboard JSON 沒生效 | provisioning 設 `updateIntervalSeconds: 30`，等 30 秒，或 `docker compose restart grafana` |
| Apple Silicon 跑慢 | 正常，image 都是 amd64，Docker Desktop 透過 Rosetta 轉譯 |

## 常用指令

```bash
docker compose ps                       # 看狀態
docker compose logs -f <service>        # 看單一服務 log
docker compose restart <service>        # 重啟單一服務
docker compose down                     # 全部關掉（保留資料 volume）
docker compose down -v                  # 全部關掉並刪除 volume（完全重置）
docker compose pull && docker compose up -d  # 拉新 image 重啟
```
