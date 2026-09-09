# 常用 SAP 表与字段速查

本表供 SAP 数据分析技能解释字段、生成查询建议时参考。

## 主数据

| 表名 | 中文名 | 常用字段 |
|---|---|---|
| KNA1 | 客户主数据（一般数据） | KUNNR 客户编码、NAME1 名称、ORT01 城市、LAND1 国家、ERDAT 创建日期 |
| KNB1 | 客户公司代码层数据 | MANDT、KUNNR、BUKRS |
| KNVV | 客户销售视图数据 | MANDT、KUNNR、VKORG、VTWEG 分销渠道 |
| KNVP | 客户合作伙伴数据 | MANDT、KUNNR、PARVW |
| LFA1 | 供应商主数据（一般数据） | LIFNR 供应商编码、NAME1 名称、ORT01 城市、LAND1 国家、ERDAT 创建日期 |
| MARA | 物料主数据（一般数据） | MATNR 物料编码、MTART 物料类型、MATKL 物料组、MEINS 基本单位 |
| MAKT | 物料描述（多语言） | MATNR 物料编码、MAKTX 物料描述、SPRAS 语言代码 |
| T001 | 公司代码 | BUKRS 公司代码、BUTXT 公司名称、LAND1 国家 |
| T001W | 工厂/地点 | WERKS 工厂、NAME1 工厂名称、ORT01 城市、LAND1 国家 |
| T001L | 库存地点 | WERKS 工厂、LGORT 库存地点、LGOBE 库存地点描述 |
| SKA1 | 总账科目主数据 | SAKNR 科目号、TXT20 科目短文本 |
| SKAT | 总账科目描述（科目表级） | MANDT、KTOPL 科目表、SAKNR 科目号、TXT50 描述 |
| SKB1 | 总账科目公司代码层数据 | MANDT、BUKRS、SAKNR |

## 销售（SD）

| 表名 | 中文名 | 常用字段 |
|---|---|---|
| VBAK | 销售凭证抬头 | VBELN 销售订单号、AUDAT 订单日期、KUNNR 客户编码、VKORG 销售组织、NETWR 净值、WAERK 货币 |
| VBAP | 销售凭证行项目 | VBELN 订单号、POSNR 行项目、MATNR 物料编码、MENGE 数量、NETPR 净价 |
| VBEP | 销售凭证计划行 | MANDT、VBELN、POSNR、ETENR 计划行号、EDATU 交货日期、MENGE |
| VBKD | 销售业务数据 | MANDT、VBELN、POSNR、BSTKD 客户采购订单号 |
| VBPA | 销售凭证合作伙伴 | MANDT、VBELN、POSNR、PARVW 合作伙伴功能、KUNNR |
| VBFA | 销售凭证流 | MANDT、VBELN、POSNR、VBELN_NACH 后续凭证号 |
| VBRK | 开票凭证抬头 | VBELN 发票号、FKDAT 发票日期、KUNNR 客户编码、NETWR 净值、WAERK 货币 |
| VBRP | 开票凭证行项目 | VBELN 发票号、POSNR 行项目、MATNR 物料编码 |

### SD 模块表关联关系

```
VBAK (销售订单抬头)
  │
  ├── VBAP (行项目) ── 关联键：MANDT + VBELN
  │     │
  │     ├── VBEP (计划行) ── 关联键：MANDT + VBELN + POSNR
  │     │
  │     ├── VBKD (业务数据) ── 关联键：MANDT + VBELN + POSNR
  │     │
  │     └── MARA (物料) ── 关联键：MATNR
  │
  ├── VBPA (合作伙伴) ── 关联键：MANDT + VBELN
  │     │
  │     └── KNA1 (客户) ── 关联键：KUNNR
  │
  └── VBFA (凭证流) ── 关联键：MANDT + VBELN

KNA1 (客户基本数据)
  ├── KNB1 (公司代码层) ── 关联键：MANDT + KUNNR + BUKRS
  └── KNVV (销售视图) ── 关联键：MANDT + KUNNR + VKORG + VTWEG
```

## 采购（MM）

| 表名 | 中文名 | 常用字段 |
|---|---|---|
| EKKO | 采购订单抬头 | EBELN 采购订单号、BEDAT 订单日期、LIFNR 供应商编码、EKORG 采购组织、WAERS 货币 |
| EKPO | 采购订单行项目 | EBELN 订单号、EBELP 行项目、MATNR 物料编码、MENGE 数量、NETPR 净价、BRTWR 总金额 |
| EBAN | 采购申请 | BANFN 申请号、BNFPO 行项目、BADAT 申请日期、MATNR 物料编码、MENGE 数量 |
| EKBE | 采购凭证历史 | EBELN 订单号、EBELP 行项目、BEWTP 历史分类、DMBTR 金额 |
| EINE | 采购信息记录-采购组织数据 | MANDT、INFNR 信息记录号、LIFNR、MATNR、EKORG |
| MARC | 物料工厂级数据 | MANDT、MATNR、WERKS 工厂、LGORT 库存地点、VPRSV 价格控制 |
| MARD | 物料工厂库存 | MATNR 物料编码、WERKS 工厂、LGORT 库存地点、LABST 非限制库存、MEINS 单位 |
| MCHB | 物料批次库存 | MATNR 物料编码、WERKS 工厂、LGORT 库存地点、CHARG 批次、CLABS 非限制库存 |
| MKOL | 供应商分包库存 | MATNR 物料编码、WERKS 工厂、LIFNR 供应商编码、SLABS 非限制库存 |
| MSLB | 客户分包库存 | MATNR 物料编码、WERKS 工厂、KUNNR 客户编码、SLABS 非限制库存 |
| MBEW | 物料评估数据（财务） | MANDT、MATNR、BUKRS、BWKEY 评估视图、VPRSV 价格控制 |
| MARM | 物料计量单位转换 | MANDT、MATNR、MEINH 计量单位、UMREZ 转换因子-分子 |

### MM 模块表关联关系

```
MARA (物料主数据通用)
  │
  ├── MARC (工厂级) ── 关联键：MANDT + MATNR
  │     │
  │     └── MARD (库存地点级) ── 关联键：MANDT + MATNR + WERKS + LGORT
  │
  ├── MAKT (描述) ── 关联键：MANDT + MATNR + SPRAS
  │
  ├── MBEW (评估) ── 关联键：MANDT + MATNR + BUKRS/BWKEY
  │
  └── MARM (单位转换) ── 关联键：MANDT + MATNR

EKKO (采购订单抬头)
  │
  └── EKPO (行项目) ── 关联键：MANDT + EBELN
        │
        ├── EKBE (历史) ── 关联键：MANDT + EBELN + EBELP
        │
        └── MARA (物料) ── 关联键：MATNR
```

## 财务（FI）

| 表名 | 中文名 | 常用字段 |
|---|---|---|
| BKPF | 会计核算凭证抬头 | BUKRS 公司代码、BELNR 凭证号、GJAHR 会计年度、BLDAT 凭证日期、BUDAT 过账日期、MONAT 期间、WAERS 货币 |
| BSEG | 会计核算凭证行项目（簇表） | MANDT、BUKRS、BELNR、GJAHR、BUZEI 行项目编号、HKONT 总账科目、WRBTR 金额、SHKZG 借贷标识 |
| BSIS | 总账未清项（S-未清） | MANDT、BUKRS、BELNR、GJAHR、BUZEI、HKONT |
| BSAS | 总账已清项（S-已清） | 同上 |
| BSID | 客户未清项（D-未清） | MANDT、BUKRS、BELNR、GJAHR、BUZEI、KUNNR 客户号 |
| BSAD | 客户已清项（D-已清） | 同上 |
| BSIK | 供应商未清项（K-未清） | MANDT、BUKRS、BELNR、GJAHR、BUZEI、LIFNR 供应商号 |
| BSAK | 供应商已清项（K-已清） | 同上 |

### FI 模块表关联关系

```
BKPF (抬头) ──┬── BSEG (行项目)
              │   关联键：BUKRS + BELNR + GJAHR
              │
              ├── BSIS/BSAS (总账未清/已清)
              │   关联键：BUKRS + BELNR + GJAHR + BUZEI
              │
              ├── BSID/BSAD (客户未清/已清)
              │   关联键：BUKRS + BELNR + GJAHR + BUZEI
              │
              └── BSIK/BSAK (供应商未清/已清)
                  关联键：BUKRS + BELNR + GJAHR + BUZEI
```

> 说明：记账时数据写入 BKPF 和 BSEG，同时根据需要写入对应的未清表；清账时从未清表删除并插入已清表。BSEG 为簇表，建议通过 BKPF 关联后按主键取数。

## 库存/物料凭证

| 表名 | 中文名 | 常用字段 |
|---|---|---|
| MKPF | 物料凭证抬头 | MANDT、MBLNR 物料凭证号、MJAHR 物料年度、BUDAT 过账日期 |
| MSEG | 物料凭证行项目 | MANDT、MBLNR、MJAHR、ZEILE 行号、MATNR、MENGE、BUKRS |

> S/4HANA 说明：S/4HANA 中 MKPF/MSEG 已被 **MATDOC**（物料凭证通用表）替代。

## 生产（PP）

| 表名 | 中文名 | 常用字段 |
|---|---|---|
| AUFK | 生产订单抬头 | MANDT、AUFNR 订单号、AUART 订单类型、KDAUF 相关销售订单、WERKS 工厂 |
| AFKO | PP 订单抬头数据 | MANDT、AUFNR、KDAUF、AUART |
| AFPO | 生产订单行项目 | MANDT、AUFNR、POSNR |
| JEST | 对象状态 | MANDT、OBJNR 对象号、STAT 状态、INACT 激活标识 |
| TJ02T | 状态文本 | MANDT、ISTAT 状态、SPRAS、TXT30 描述 |

### PP 模块表关联关系

```
AUFK (生产订单抬头) ──┬── AFKO (订单抬头详细) ── 关联键：AUFNR
                    │
                    └── AFPO (订单行项目) ── 关联键：AUFNR

AUFK.OBJNR (对象号) ── JEST (状态) ── 关联键：OBJNR
JEST.STAT ── TJ02T (状态文本) ── 关联键：ISTAT
```

## 成本（CO）

| 表名 | 中文名 | 常用字段 |
|---|---|---|
| COEP | CO 实际过账行项目 | MANDT、OBJNR 对象号、GJAHR、PERIO 期间、WRTTP 值类型 |
| COBK | CO 凭证抬头 | MANDT、COBLNR CO 凭证号、BUKRS |
| CSKA | 成本要素主数据（科目表级） | MANDT、KTOPL、KSTAR 成本要素 |
| CSKB | 成本要素主数据（控制范围级） | MANDT、KOKRS 控制范围、KSTAR |

## 跨模块关键关联

| 关联路径 | 关联字段 |
|---|---|
| VBAP → MARA | VBAP.MATNR ↔ MARA.MATNR |
| MSEG → BKPF | MSEG.XAWKEY ↔ BKPF.AWKEY |
| MSEG → BSEG | 物料凭证与财务凭证通过 AWKEY 关联 |
| VBAK → BKPF | 销售订单通过 VBELN 与会计凭证关联（AWKEY） |
| BSID/BSAD → KNA1 | BSID.KUNNR ↔ KNA1.KUNNR |
| BSIK/BSAK → LFA1 | BSIK.LIFNR ↔ LFA1.LIFNR |
| AUFK → VBAK | AUFK.KDAUF ↔ VBAK.VBELN |

## 通用关键字段

| 字段 | 说明 | 出现表 |
|---|---|---|
| MANDT | 集团（Client），所有表共有 | 所有 SAP 表 |
| BUKRS | 公司代码 | BKPF、BSEG、BSIS/BSAS/BSID/BSAD/BSIK/BSAK、T001、SKB1 等 |
| MATNR | 物料号 | MARA、MARC、MARD、MAKT、MBEW、MSEG、EKPO、VBAP 等 |
| KUNNR | 客户号 | KNA1、KNB1、KNVV、KNVP、VBAK、BSID/BSAD 等 |
| LIFNR | 供应商号 | EKKO、BSIK/BSAK、LFA1 等 |
| WERKS | 工厂 | MARC、MARD、AUFK 等 |
| BELNR | 凭证编号 | BKPF、BSEG 及六张未清/已清表 |
| VBELN | 销售凭证号 | VBAK、VBAP、VBEP、VBKD、VBPA、VBFA 等 |
| AUFNR | 生产订单号 | AUFK、AFKO、AFPO 等 |
| EBELN | 采购凭证号 | EKKO、EKPO、EKBE、EBAN 等 |

## SAP 表命名规则速查

| 前缀 | 模块 | 示例 |
|---|---|---|
| B* | FI（财务） | BKPF、BSEG |
| C* | CO（成本控制） | CSKA、CSKB、COEP |
| E* / M* | MM（物料管理） | EKKO、EKPO、MARA |
| V* | SD（销售分销） | VBAK、VBAP |
| A* / F* | PP（生产计划） | AFKO、AFPO |

---

> 补充说明：
> 1. 所有表均以 **MANDT**（集团）作为第一主键，跨集团数据隔离。
> 2. 以上表关系可在 SAP 事务码 **SE11** 中查看完整的外键定义。
> 3. S/4HANA 引入了 **ACDOCA**（通用日记账）统一 FI/CO 数据存储；MM 模块引入了 **MATDOC** 替代 MKPF/MSEG。
