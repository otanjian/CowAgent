## 1，SAP中 **BAPI** 的核心概念

------

### **1-1，BAPI 的定义**

**BAPI（Business Application Programming Interface）** 是SAP提供的标准化编程接口，用于与SAP系统进行业务数据交互。
它是基于 **面向对象设计** 的业务功能封装，通过 **RFC（Remote Function Call）** 技术实现，支持外部系统或程序（如Java、.NET、Excel等）远程调用SAP的核心业务逻辑。

------

### **1-2，BAPI 的核心特征**

1. **标准化接口：**遵循SAP预定义的命名规范和参数结构，确保跨系统兼容性。

   示例：

   ```undefined
   BAPI_SALESORDER_CREATEFROMDAT2
   ```

   （创建销售订单）。

2. **业务对象（Business Object）关联：**每个BAPI通常对应一个 **业务对象**（如销售订单、采购订单），通过 **BOR（Business Object Repository）** 管理。

   例如：通过业务对象

    

   ```ebnf
   SalesOrder
   ```

    

   调用其方法

    

   ```ebnf
   Create
   ```

   。

3. **事务控制：**需显式调用 `BAPI_TRANSACTION_COMMIT` 提交数据，或 `BAPI_TRANSACTION_ROLLBACK` 回滚操作，确保数据一致性。

4. **数据封装：**隐藏SAP底层表结构，通过输入/输出参数传递业务数据(如订单抬头、行项目等）

5. **跨系统兼容：**支持跨系统调用（如SAP与非SAP系统集成），通过RFC协议实现远程通信。

------

### **1-3，BAPI 的主要应用场景**

1. **系统集成：**与外部系统（如ERP、CRM、MES）进行数据交互（如创建订单、查询库存）。
2. **自动化处理：**批量处理业务操作（如自动过账财务凭证、生成交货单）。
3. **数据交互：**读取或更新主数据（如物料、客户、供应商）。
4. **定制开发：**扩展SAP标准功能（例如通过自定义BAPI增强业务逻辑）。

------

### **1-4，BAPI 的注意事项**

1. **权限检查：**调用BAPI需确保用户具有相应的SAP权限（如事务代码权限、字段级权限）。
2. **输入校验：**调用前需验证输入参数（如必填字段、格式校验），避免触发SAP短文本报错（`SY-SUBRC ≠ 0`）。
3. **事务提交：**调用修改类BAPI后，需显式提交事务（`BAPI_TRANSACTION_COMMIT`），否则数据不会保存。
4. **错误处理：**检查返回参数 `RETURN`（包含错误消息代码和描述），处理异常情况。

------

### **1-5，BAPI vs 其他接口**

| **接口类型**        | **特点**                                                     |
| :------------------ | :----------------------------------------------------------- |
| **BAPI**            | 基于业务对象，标准化，支持事务控制，适合业务逻辑操作（如创建订单）。 |
| **RFC（函数模块）** | 通用远程调用，灵活性高，但需自行处理业务逻辑和校验。         |
| **IDoc**            | 基于消息的异步通信，适合大批量数据传输（如物料主数据同步）。 |

------

### **1-6，总结**

BAPI是SAP系统集成的核心工具，通过标准化接口实现业务逻辑的封装与复用，适用于跨系统交互和复杂业务流程自动化。实际开发中需结合业务需求选择合适的BAPI，并严格遵循SAP的数据规范与事务管理机制。



## 2，SAP中 常用的 BAPI 一览

### **2-1，MM模块（物料管理 / Material Management）**

| BAPI名称                        | English                          | 中文                   | 日本語                |
| :------------------------------ | :------------------------------- | :--------------------- | :-------------------- |
| **BAPI_PR_CREATE**              | Create Purchase Requisition      | 创建采购申请           | 購買依頼作成          |
| **BAPI_PR_CHANGE**              | Change Purchase Requisition      | 修改采购申请           | 購買依頼変更          |
| **BAPI_PO_CREATE1**             | Create Purchase Order            | 创建采购订单           | 購買発注作成          |
| **BAPI_PO_CHANGE**              | Change Purchase Order            | 修改采购订单           | 購買発注変更          |
| **BAPI_PO_GETDETAIL**           | Get Purchase Order Details       | 获取采购订单详情       | 購買発注詳細取得      |
| **BAPI_MATERIAL_SAVEDATA**      | Save Material Master Data        | 保存物料主数据         | マテリアルマスタ保存  |
| **BAPI_MATERIAL_GETLIST**       | Get Material List                | 获取物料列表           | マテリアルリスト取得  |
| **BAPI_MATERIAL_GET_DETAIL**    | Get Material Details             | 获取物料详情           | マテリアル詳細取得    |
| **BAPI_GOODSMVT_CREATE**        | Create Goods Movement            | 创建物料凭证           | 入出庫伝票作成        |
| **BAPI_RESERVATION_CREATE1**    | Create Reservation               | 创建预留               | リザベーション作成    |
| **BAPI_STOCK_LIST**             | Get Stock List                   | 获取库存列表           | 在庫リスト取得        |
| **BAPI_INSPLOT_CREATE**         | Create Inspection Lot            | 创建检验批             | 検査ロット作成        |
| **BAPI_REQUISITION_CREATE**     | Create Purchase Requisition (MM) | 创建采购申请（MM模块） | 購買依頼作成（MM）    |
| **BAPI_CONTRACT_CREATE**        | Create Contract                  | 创建合同               | 契約作成              |
| **BAPI_SERVICEENTRY_CREATE**    | Create Service Entry Sheet       | 创建服务确认单         | サービス確認書作成    |
| **BAPI_PO_RELEASE**             | Release Purchase Order           | 释放采购订单           | 購買発注リリース      |
| **BAPI_PO_DELETE**              | Delete Purchase Order            | 删除采购订单           | 購買発注削除          |
| **BAPI_MATERIAL_BOM_GETDETAIL** | Get Material BOM Details         | 获取物料BOM详情        | マテリアルBOM詳細取得 |
| **BAPI_MATERIAL_BOM_CREATE**    | Create Material BOM              | 创建物料BOM            | マテリアルBOM作成     |
| **BAPI_MATERIAL_PRICE_ADD**     | Add Material Price               | 添加物料价格           | マテリアル価格追加    |

### **2-2，SD模块（销售与分销 / Sales and Distribution）**

| BAPI名称                           | English                           | 中文             | 日本語                       |
| :--------------------------------- | :-------------------------------- | :--------------- | :--------------------------- |
| **BAPI_SALESORDER_CREATEFROMDAT2** | Create Sales Order                | 创建销售订单     | 販売オーダー作成             |
| **BAPI_SALESORDER_CHANGE**         | Change Sales Order                | 修改销售订单     | 販売オーダー変更             |
| **BAPI_SALESORDER_GETLIST**        | Get Sales Order List              | 获取销售订单列表 | 販売オーダーリスト取得       |
| **BAPI_SALESORDER_GETDETAIL**      | Get Sales Order Details           | 获取销售订单详情 | 販売オーダー詳細取得         |
| **BAPI_DELIVERY_CREATE**           | Create Delivery Document          | 创建交货单       | 出荷伝票作成                 |
| **BAPI_DELIVERY_GETLIST**          | Get Delivery List                 | 获取交货单列表   | 出荷伝票リスト取得           |
| **BAPI_BILLINGDOC_CREATEMULTIPLE** | Create Multiple Billing Documents | 创建多个发票     | 請求書複数作成               |
| **BAPI_BILLINGDOC_CANCEL**         | Cancel Billing Document           | 取消发票         | 請求書取消                   |
| **BAPI_CUSTOMER_CREATEFROMDATA**   | Create Customer Master            | 创建客户主数据   | 顧客マスタ作成               |
| **BAPI_CUSTOMER_CHANGE**           | Change Customer Master            | 修改客户主数据   | 顧客マスタ変更               |
| **BAPI_CUSTOMER_GETDETAIL**        | Get Customer Details              | 获取客户详情     | 顧客詳細取得                 |
| **BAPI_PRICING_CONDITIONS**        | Get Pricing Conditions            | 获取定价条件     | 価格条件取得                 |
| **BAPI_OUTB_DELIVERY_CONFIRM**     | Confirm Outbound Delivery         | 确认外向交货     | 出荷確認                     |
| **BAPI_REPLENISHMENT_CREATE**      | Create Replenishment Order        | 创建补货订单     | 補充オーダー作成             |
| **BAPI_CREDITMEMO_CREATE**         | Create Credit Memo                | 创建贷项凭证     | 貸方伝票作成                 |
| **BAPI_SALESORDER_SIMULATE**       | Simulate Sales Order              | 模拟销售订单     | 販売オーダーシミュレーション |
| **BAPI_SALESORDER_GETSTATUS**      | Get Sales Order Status            | 获取销售订单状态 | 販売オーダーステータス取得   |
| **BAPI_ORDER_SCHEDULELINE_CHANGE** | Change Schedule Line              | 修改计划行       | スケジュール行変更           |
| **BAPI_ORDER_PARTNER_CHANGE**      | Change Order Partner              | 修改订单合作伙伴 | オーダーパートナー変更       |
| **BAPI_ORDER_BILLINGPLAN_CHANGE**  | Change Billing Plan               | 修改开票计划     | 請求計画変更                 |

------

### **2-3，FI模块（财务会计 / Financial Accounting）**

| BAPI名称                           | English                     | 中文               | 日本語               |
| :--------------------------------- | :-------------------------- | :----------------- | :------------------- |
| **BAPI_ACC_DOCUMENT_POST**         | Post Accounting Document    | 过账会计凭证       | 会計伝票転記         |
| **BAPI_ACC_DOCUMENT_REV_POST**     | Reverse Accounting Document | 冲销会计凭证       | 会計伝票取消転記     |
| **BAPI_ACC_DOCUMENT_CHECK**        | Check Accounting Document   | 检查会计凭证       | 会計伝票チェック     |
| **BAPI_GL_ACC_GETLIST**            | Get G/L Account List        | 获取总账科目列表   | G/L勘定リスト取得    |
| **BAPI_AP_ACC_GETKEYDATEBALANCE**  | Get Vendor Account Balance  | 获取供应商账户余额 | 仕入先残高取得       |
| **BAPI_AR_ACC_GETOPENITEMS**       | Get Customer Open Items     | 获取客户未清项     | 顧客未処理項目取得   |
| **BAPI_FIXEDASSET_GETDETAIL1**     | Get Fixed Asset Details     | 获取固定资产详情   | 固定資産詳細取得     |
| **BAPI_BANK_GETDETAIL**            | Get Bank Details            | 获取银行详情       | 銀行詳細取得         |
| **BAPI_CURRENCY_GETRATETYPE**      | Get Currency Rate Type      | 获取货币汇率类型   | 通貨レートタイプ取得 |
| **BAPI_TAX_GETSTATUS**             | Get Tax Status              | 获取税务状态       | 税ステータス取得     |
| **BAPI_COMPANY_GETDATA**           | Get Company Data            | 获取公司数据       | 会社データ取得       |
| **BAPI_ACC_ACTIVITY_ALLOC_POST**   | Post Activity Allocation    | 过账作业分配       | 活動配賦転記         |
| **BAPI_ACC_COST_CENTER_POST**      | Post Cost Center Accounting | 过账成本中心会计   | コストセンタ会計転記 |
| **BAPI_ACC_BILLING_POST**          | Post Billing Document       | 过账开票凭证       | 請求伝票転記         |
| **BAPI_ACC_INV_TAX_POST**          | Post Tax on Invoice         | 过账发票税金       | 請求書税金転記       |
| **BAPI_ACC_EMPLOYEE_EXPENSE_POST** | Post Employee Expense       | 过账员工费用       | 従業員費用転記       |
| **BAPI_ACC_TAX_CODE_GETLIST**      | Get Tax Code List           | 获取税码列表       | 税コードリスト取得   |
| **BAPI_ACC_ACCOUNT_GETBALANCE**    | Get Account Balance         | 获取账户余额       | 勘定残高取得         |
| **BAPI_ACC_ACCOUNT_GETLIST**       | Get Account List            | 获取账户列表       | 勘定リスト取得       |
| **BAPI_ACC_ACCOUNT_GETDETAIL**     | Get Account Details         | 获取账户详情       | 勘定詳細取得         |

------

### **2-3，CO模块（管理会计 / Controlling）**

| BAPI名称                             | English                          | 中文                   | 日本語                         |
| :----------------------------------- | :------------------------------- | :--------------------- | :----------------------------- |
| **BAPI_COSTCENTER_GETLIST**          | Get Cost Center List             | 获取成本中心列表       | コストセンタリスト取得         |
| **BAPI_COSTCENTER_GETCOSTCENTER**    | Get Cost Center Details          | 获取成本中心详情       | コストセンタ詳細取得           |
| **BAPI_COSTACTPLN_POSTPRIMCOST**     | Post Primary Costs               | 过账初级成本           | 一次コスト転記                 |
| **BAPI_COSTACTPLN_POSTSECCOST**      | Post Secondary Costs             | 过账次级成本           | 二次コスト転記                 |
| **BAPI_COSTESTIMATE_CREATE**         | Create Cost Estimate             | 创建成本估算           | 原価見積作成                   |
| **BAPI_COSTESTIMATE_GETDETAIL**      | Get Cost Estimate Details        | 获取成本估算详情       | 原価見積詳細取得               |
| **BAPI_ACTIVITYTYPE_GETLIST**        | Get Activity Type List           | 获取作业类型列表       | 活動タイプリスト取得           |
| **BAPI_ACTIVITYTYPE_GETDETAIL**      | Get Activity Type Details        | 获取作业类型详情       | 活動タイプ詳細取得             |
| **BAPI_INTERNALORDER_CREATE**        | Create Internal Order            | 创建内部订单           | 内部オーダー作成               |
| **BAPI_INTERNALORDER_CHANGE**        | Change Internal Order            | 修改内部订单           | 内部オーダー変更               |
| **BAPI_PROFITCENTER_GETLIST**        | Get Profit Center List           | 获取利润中心列表       | プロフィットセンターリスト取得 |
| **BAPI_PROFITCENTER_GETDETAIL**      | Get Profit Center Details        | 获取利润中心详情       | プロフィットセンター詳細取得   |
| **BAPI_COSTOBJECT_GETLIST**          | Get Cost Object List             | 获取成本对象列表       | コストオブジェクトリスト取得   |
| **BAPI_COSTOBJECT_GETDETAIL**        | Get Cost Object Details          | 获取成本对象详情       | コストオブジェクト詳細取得     |
| **BAPI_ACTIVITY_ALLOCATION_POST**    | Post Activity Allocation         | 过账作业分配           | 活動配賦転記                   |
| **BAPI_COSTELEMENT_GETLIST**         | Get Cost Element List            | 获取成本要素列表       | コスト要素リスト取得           |
| **BAPI_COSTELEMENT_GETDETAIL**       | Get Cost Element Details         | 获取成本要素详情       | コスト要素詳細取得             |
| **BAPI_COSTCENTER_HIERARCHY_GET**    | Get Cost Center Hierarchy        | 获取成本中心层级       | コストセンタ階層取得           |
| **BAPI_COSTCENTER_ACTIVITY_POST**    | Post Cost Center Activity        | 过账成本中心活动       | コストセンタ活動転記           |
| **BAPI_COSTCENTER_PLANNEDCOST_POST** | Post Planned Cost to Cost Center | 过账计划成本到成本中心 | 計画コスト転記（コストセンタ） |

------

### **2-4，PP模块（生产计划 / Production Planning）**

| BAPI名称                             | English                             | 中文               | 日本語                       |
| :----------------------------------- | :---------------------------------- | :----------------- | :--------------------------- |
| **BAPI_PRODUCTIONORDER_CREATE**      | Create Production Order             | 创建生产订单       | 生産オーダー作成             |
| **BAPI_PRODUCTIONORDER_GET_DETAIL**  | Get Production Order Details        | 获取生产订单详情   | 生産オーダー詳細取得         |
| **BAPI_PRODUCTIONORDER_CONF_CREATE** | Create Production Confirmation      | 创建生产确认       | 生産確認作成                 |
| **BAPI_PRODORD_CONFIRM**             | Confirm Production Order            | 确认生产订单       | 生産オーダー確認             |
| **BAPI_PLANNEDORDER_CONVERT**        | Convert Planned Order to Production | 计划订单转生产订单 | 計画オーダー生産オーダー変換 |
| **BAPI_MATERIAL_AVAILABILITY**       | Check Material Availability         | 检查物料可用性     | マテリアル可用性確認         |
| **BAPI_PP_ORDER_CHANGE**             | Change Production Order             | 修改生产订单       | 生産オーダー変更             |
| **BAPI_PP_ORDER_DELETE**             | Delete Production Order             | 删除生产订单       | 生産オーダー削除             |
| **BAPI_PP_ORDER_RELEASE**            | Release Production Order            | 释放生产订单       | 生産オーダーリリース         |
| **BAPI_PP_ORDER_GETLIST**            | Get Production Order List           | 获取生产订单列表   | 生産オーダーリスト取得       |
| **BAPI_PP_ORDER_GETDETAIL**          | Get Production Order Details        | 获取生产订单详情   | 生産オーダー詳細取得         |
| **BAPI_PP_ORDER_OPERATION_POST**     | Post Production Order Operation     | 过账生产订单工序   | 生産オーダー作業転記         |
| **BAPI_PP_ORDER_COMPONENT_POST**     | Post Production Order Component     | 过账生产订单组件   | 生産オーダー部品転記         |
| **BAPI_ROUTING_CREATE**              | Create Routing                      | 创建工艺路线       | ルーティング作成             |
| **BAPI_ROUTING_GETDETAIL**           | Get Routing Details                 | 获取工艺路线详情   | ルーティング詳細取得         |
| **BAPI_WORKCENTER_GETLIST**          | Get Work Center List                | 获取工作中心列表   | 作業センターリスト取得       |
| **BAPI_WORKCENTER_GETDETAIL**        | Get Work Center Details             | 获取工作中心详情   | 作業センター詳細取得         |
| **BAPI_PLANNEDORDER_GETDETAIL**      | Get Planned Order Details           | 获取计划订单详情   | 計画オーダー詳細取得         |
| **BAPI_PLANNEDORDER_CHANGE**         | Change Planned Order                | 修改计划订单       | 計画オーダー変更             |
| **BAPI_PLANNEDORDER_DELETE**         | Delete Planned Order                | 删除计划订单       | 計画オーダー削除             |

------

 

### **2-5，PM模块（工厂维护 / Plant Maintenance）**



| BAPI名称                    | English                    | 中文         | 日本語           |
| :-------------------------- | :------------------------- | :----------- | :--------------- |
| **BAPI_ALM_NOTIF_CREATE**   | Create Notification        | 创建通知单   | 通知書作成       |
| **BAPI_ALM_ORDER_MAINTAIN** | Maintain Maintenance Order | 维护工单     | 保全オーダー保守 |
| **BAPI_ALM_EQUI_GETDETAIL** | Get Equipment Details      | 获取设备详情 | 設備詳細取得     |

### **2-6，HR模块（人力资源 / Human Resources）**

| BAPI名称                  | English              | 中文         | 日本語               |
| :------------------------ | :------------------- | :----------- | :------------------- |
| **BAPI_EMPLOYEE_GETLIST** | Get Employee List    | 获取员工列表 | 従業員リスト取得     |
| **BAPI_EMPLOYEE_ENQUEUE** | Lock Employee Record | 锁定员工记录 | 従業員レコードロック |

------

### **2-7，通用BAPI (General BAPIs)**

| BAPI名称                      | English               | 中文             | 日本語               |
| :---------------------------- | :-------------------- | :--------------- | :------------------- |
| **BAPI_TRANSACTION_COMMIT**   | Commit Transaction    | 提交事务         | トランザクション確定 |
| **BAPI_TRANSACTION_ROLLBACK** | Rollback Transaction  | 回滚事务         | トランザクション取消 |
| **BAPI_COMPANYCODE_GETLIST**  | Get Company Code List | 获取公司代码列表 | 会社コードリスト取得 |
| **BAPI_USER_GET_DETAIL**      | Get User Details      | 获取用户详细信息 | ユーザー詳細情報取得 |

### **2-8，注意事项**

1. **BAPI版本兼容性：**部分BAPI在SAP S/4HANA中被替代（如使用CDS View或RAP模型）。
2. **参数规范：**输入参数需严格遵循SAP数据字典定义（例如日期格式 `YYYYMMDD`）。
3. **事务管理：**修改类BAPI需显式调用 `BAPI_TRANSACTION_COMMIT` 提交事务。

### 2-9，使用BAPI 时的步骤

1. 调用BAPI函数
2. 检查RETURN参数中的错误信息
3. 使用BAPI_TRANSACTION_COMMIT提交或BAPI_TRANSACTION_ROLLBACK回滚