<template>
  <div class="app-container">
    <el-form :model="queryParams" ref="queryRef" :inline="true" v-show="showSearch" label-width="68px">
      <el-form-item label="订单编号" prop="orderNo">
        <el-input
          v-model="queryParams.orderNo"
          placeholder="请输入订单编号"
          clearable
          @keyup.enter="handleQuery"
        />
      </el-form-item>
      <el-form-item label="创建时间" style="width: 308px">
        <el-date-picker
          v-model="dateRange"
          value-format="YYYY-MM-DD"
          type="daterange"
          range-separator="-"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
        ></el-date-picker>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" icon="Search" @click="handleQuery">搜索</el-button>
        <el-button icon="Refresh" @click="resetQuery">重置</el-button>
      </el-form-item>
    </el-form>


    <el-table v-loading="loading" :data="orderList" @selection-change="handleSelectionChange">
      <el-table-column label="序号" type="index" align="center" prop="id" width="80"/>
      <el-table-column label="订单编号" align="center" prop="orderNo" show-overflow-tooltip="" />
      <el-table-column label="商品名称" align="center" prop="skuName" />
      <el-table-column label="订单状态" align="center" prop="status">
      <template #default="scope">
        <el-tag :type="getOrderStatusType(scope.row.status)">
          {{ getOrderStatusLabel(scope.row.status) }}
        </el-tag>
      </template>
      </el-table-column>
      <el-table-column label="订单金额" align="center" prop="amount" />
      <el-table-column label="订单时间" align="center" prop="createTime" width="180">
        <template #default="scope">
          <span>{{ parseTime(scope.row.createTime, '{y}-{m}-{d} {h}:{i}:{s}') }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" align="center" class-name="small-padding fixed-width">
        <template #default="scope">
          <el-button link type="primary"  @click="handleUpdate(scope.row)" v-hasPermi="['manage:order:query']">查看详情</el-button>
        </template>
      </el-table-column>
    </el-table>
    
    <pagination
      v-show="total>0"
      :total="total"
      v-model:page="queryParams.pageNum"
      v-model:limit="queryParams.pageSize"
      @pagination="getList"
    />

    <!-- 查看详情对话框 -->
      <el-dialog :title="title" v-model="open" width="500px" append-to-body>
      <el-form ref="orderRef" :model="form" :rules="rules" label-width="80px">
        <el-form-item label="订单编号" prop="orderNo">
          <span>{{ form.orderNo }}</span>
        </el-form-item>
        <el-form-item label="商品名称" prop="skuName">
          <span>{{ form.skuName }}</span>
        </el-form-item>
        <el-form-item label="订单金额" prop="amount">
          <span>{{ form.amount }}</span>
        </el-form-item>
        <el-form-item label="订单状态" prop="status">
          <span>{{ getOrderStatusLabel(form.status) }}</span>
        </el-form-item>
        <el-form-item label="设备编号" prop="orderNo">
          <span>{{ form.orderNo }}</span>
        </el-form-item>
        <el-form-item label="设备地址" prop="addr">
          <span>{{ form.addr }}</span>
        </el-form-item>
        <el-form-item label="创建时间" prop="createTime">
          <span>{{ form.createTime }}</span>
        </el-form-item>
        <el-form-item label="完成时间" prop="updateTime">
          <span>{{ form.updateTime }}</span>
        </el-form-item>
        <el-form-item label="支付方式" prop="payType">
          <span>{{ form.payType }}</span>
        </el-form-item>
        <el-form-item label="交易订单号" prop="transactionId">
          <span>{{ form.transactionId }}</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="dialog-footer">
          <el-button v-if="form.status === 1" type="primary" @click="handleRefund">退款</el-button>
          <el-button v-if="form.status === 0" type="danger" @click="handleCancel">取消</el-button>
          <el-button @click="cancel">关闭</el-button>
        </div>
      </template>
    </el-dialog>
     <!-- 取消订单对话框 -->
    <el-dialog title="取消订单" v-model="cancelDialogVisible" width="400px">
      <el-form ref="cancelFormRef" :model="cancelForm" label-width="80px">
        <el-form-item label="取消原因" prop="reason">
          <el-input type="textarea" v-model="cancelForm.reason" placeholder="请输入取消原因"></el-input>
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="dialog-footer">
          <el-button @click="cancelDialogVisible = false">关闭</el-button>
          <el-button type="primary" @click="confirmCancel">确定</el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup name="Order">
import { listOrder, getOrder, delOrder, addOrder, updateOrder } from "@/api/manage/order";
import { loadAllParams } from "@/api/page";
const { proxy } = getCurrentInstance();
const { order_status } = proxy.useDict('order_status');

const orderList = ref([]);
const open = ref(false);
const loading = ref(true);
const showSearch = ref(true);
const ids = ref([]);
const single = ref(true);
const multiple = ref(true);
const total = ref(0);
const title = ref("");
const dateRange = ref([]);
const cancelDialogVisible = ref(false);
const cancelForm = reactive({
  reason: ""
});

const data = reactive({
  form: {},
  queryParams: {
    pageNum: 1,
    pageSize: 10,
    orderNo: null,
    createTime: null,
  },
  rules: {
  }
});

const { queryParams, form, rules } = toRefs(data);


// 订单状态格式化函数
function getOrderStatusLabel(status) {
  const statusMap = {
    0: '待支付',
    1: '支付完成',
    2: '出货成功',
    3: '出货失败',
    4: '已取消'
  };
  return statusMap[status] || '未知状态';
}

function getOrderStatusType(status) {
  const typeMap = {
    0: 'warning',    // 待支付 - 黄色
    1: 'success',    // 支付完成 - 绿色
    2: 'primary',    // 出货成功 - 蓝色
    3: 'danger',     // 出货失败 - 红色
    4: 'info'        // 已取消 - 灰色
  };
  return typeMap[status] || 'info';
}

/** 查询订单列表 */
function getList() {
  loading.value = true;
  listOrder(proxy.addDateRange(queryParams.value, dateRange.value)).then(response => {
    orderList.value = response.rows;
    total.value = response.total;
    loading.value = false;
  });
}

// 取消按钮
function cancel() {
  open.value = false;
  reset();
}


function handleQuery() {
  queryParams.value.pageNum = 1;
  getList();
}

function resetQuery() {
  dateRange.value = [];
  proxy.resetForm("queryRef");
  handleQuery();
}

function handleUpdate(row) {
  const _id = row.id || ids.value;
  getOrder(_id).then(response => {
    form.value = response.data;
    open.value = true;
    title.value = "订单详情";
  });
}

function handleRefund() {
  refundOrder(form.value.id).then(() => {
    open.value = false;
    getList();
  }).catch(error => {
    console.error("退款失败", error);
  });
}

function handleCancel() {
  cancelDialogVisible.value = true;
}

function confirmCancel() {
  cancelOrder(form.value.id, cancelForm.reason).then(() => {
    cancelDialogVisible.value = false;
    open.value = false;
    getList();
  }).catch(error => {
    console.error("取消订单失败", error);
  });
}


getList();
</script>
