<template>
  <div class="app-container">
    <el-form :model="queryParams" ref="queryRef" :inline="true" v-show="showSearch" label-width="68px">
      <!-- 假设可以按商品名称搜索 -->
      <el-form-item label="商品名称" prop="skuName">
        <el-input
          v-model="queryParams.skuName"
          placeholder="请输入商品名称"
          clearable
          @keyup.enter="handleQuery"
        />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" icon="Search" @click="handleQuery">搜索</el-button>
        <el-button icon="Refresh" @click="resetQuery">重置</el-button>
        <el-button :type="queryType === 'low' ? 'warning' : ''" :plain="queryType !== 'low'" icon="Warning" @click="handleLowStock">低库存预警</el-button>
        <el-button :type="queryType === 'out' ? 'danger' : ''" :plain="queryType !== 'out'" icon="CircleClose" @click="handleOutOfStock">缺货详情</el-button>
        <el-button type="success" plain icon="MagicStick" @click="handleRestockSuggestions">补货建议</el-button>
      </el-form-item>
    </el-form>

    <el-row :gutter="10" class="mb8">
      <el-col :span="1.5">
        <el-button
          type="primary"
          plain
          icon="Plus"
          @click="handleAdd"
          v-hasPermi="['manage:inventory:add']"
        >新增</el-button>
      </el-col>
      <el-col :span="1.5">
        <el-button
          type="success"
          plain
          icon="Edit"
          :disabled="single"
          @click="handleUpdate"
          v-hasPermi="['manage:inventory:edit']"
        >修改</el-button>
      </el-col>
      <el-col :span="1.5">
        <el-button
          type="danger"
          plain
          icon="Delete"
          :disabled="multiple"
          @click="handleDelete"
          v-hasPermi="['manage:inventory:remove']"
        >删除</el-button>
      </el-col>
      <right-toolbar v-model:showSearch="showSearch" @queryTable="getList"></right-toolbar>
    </el-row>

    <el-table v-loading="loading" :data="inventoryList" @selection-change="handleSelectionChange">
      <el-table-column type="selection" width="55" align="center" />
      <!-- <el-table-column label="库存ID" align="center" prop="id" /> -->
      <el-table-column label="售货机id" align="center" prop="innerCode" />
      <el-table-column label="商品名称" align="center" prop="skuName" />
      <el-table-column label="货道编号" align="center" prop="channelCode" />
      <el-table-column label="当前库存" align="center" prop="quantity" />
      <el-table-column label="最大容量" align="center" prop="maxQuantity" />
      <el-table-column label="最小容量" align="center" prop="minQuantity" />
      <el-table-column label="修改时间" align="center" prop="lastSupplyTime" width="180">
        <template #default="scope">
          <span>{{ parseTime(scope.row.updateTime) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" align="center" class-name="small-padding fixed-width">
        <template #default="scope">
          <el-button link type="primary" icon="Edit" @click="handleUpdate(scope.row)" v-hasPermi="['manage:inventory:edit']">修改</el-button>
          <el-button link type="primary" icon="Delete" @click="handleDelete(scope.row)" v-hasPermi="['manage:inventory:remove']">删除</el-button>
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

    <!-- 添加或修改库存对话框 -->
    <el-dialog :title="title" v-model="open" width="500px" append-to-body>
      <el-form ref="inventoryRef" :model="form" :rules="rules" label-width="80px">
        <!-- 根据实际字段调整 -->
        <el-form-item label="售货机id" prop="innerCode">
          <el-input v-model="form.innerCode" placeholder="请输入售货机编号" />
        </el-form-item>
        <el-form-item label="商品名称" prop="skuName">
             <el-input v-model="form.skuName" placeholder="请输入商品名称" />
        </el-form-item>
         <el-form-item label="货道编号" prop="channelCode">
             <el-input v-model="form.channelCode" placeholder="请输入货道编号" />
        </el-form-item>
        <el-form-item label="当前库存" prop="quantity">
          <el-input-number v-model="form.quantity" placeholder="请输入当前库存" />
        </el-form-item>
        <el-form-item label="最大容量" prop="maxQuantity">
          <el-input-number v-model="form.maxQuantity" placeholder="请输入最大容量" />
        </el-form-item>
    
         <el-form-item label="最小容量" prop="minQuantity">
          <el-input-number v-model="form.minQuantity" placeholder="请输入最小容量" />
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="dialog-footer">
          <el-button type="primary" @click="submitForm">确 定</el-button>
          <el-button @click="cancel">取 消</el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 补货建议对话框 -->
    <el-dialog title="补货建议" v-model="restockOpen" width="1000px" append-to-body>
        <el-table :data="restockList" style="width: 100%">
            <el-table-column prop="innerCode" label="设备编号" width="150" />
            <el-table-column prop="skuName" label="商品名称" width="150" />
            <el-table-column prop="channelCode" label="货道编号" width="150" />
            <el-table-column prop="currentQuantity" label="当前库存" />
            <el-table-column prop="maxCapacity" label="最大容量" />
            <el-table-column prop="reason" label="建议" />
        </el-table>
    </el-dialog>
  </div>
</template>

<script setup name="Inventory">
import { listInventory, getInventory, delInventory, addInventory, updateInventory, listLowInventory, listOutOfStockInventory, getRestockSuggestions } from "@/api/manage/inventory";
import { reactive, ref, toRefs, getCurrentInstance } from "vue";

const { proxy } = getCurrentInstance();

const inventoryList = ref([]);
const open = ref(false);
const loading = ref(true);
const showSearch = ref(true);
const ids = ref([]);
const single = ref(true);
const multiple = ref(true);
const total = ref(0);
const title = ref("");
const restockOpen = ref(false);
const restockList = ref([]);
const queryType = ref('all'); // 'all', 'low', 'out'

const data = reactive({
  form: {},
  queryParams: {
    pageNum: 1,
    pageSize: 10,
    skuName: null,
  },
  rules: {
    // skuName: [
    //   { required: true, message: "商品名称不能为空", trigger: "blur" }
    // ],
  }
});

const { queryParams, form, rules } = toRefs(data);

/** 查询库存列表 */
function getList() {
  loading.value = true;
  let request = listInventory; // 默认查询全部
  if (queryType.value === 'low') {
    request = listLowInventory;
  } else if (queryType.value === 'out') {
    request = listOutOfStockInventory;
  }

  request(queryParams.value).then(response => {
    inventoryList.value = response.rows;
    total.value = response.total;
    loading.value = false;
  });
}

// 取消按钮
function cancel() {
  open.value = false;
  reset();
}

// 表单重置
function reset() {
  form.value = {
    id: null,
    innerCode: null,
    skuName: null,
    channelCode: null,
    currentCapacity: 0,
    capacity: 0,
  };
  proxy.resetForm("inventoryRef");
}

/** 搜索按钮操作 */
function handleQuery() {
  queryType.value = 'all'; // 搜索时重置为全部
  queryParams.value.pageNum = 1;
  getList();
}

/** 重置按钮操作 */
function resetQuery() {
  proxy.resetForm("queryRef");
  handleQuery();
}

/** 多选框选中数据 */
function handleSelectionChange(selection) {
  ids.value = selection.map(item => item.id);
  single.value = selection.length != 1;
  multiple.value = !selection.length;
}

/** 新增按钮操作 */
function handleAdd() {
  reset();
  open.value = true;
  title.value = "添加库存";
}

/** 修改按钮操作 */
function handleUpdate(row) {
  reset();
  const _id = row.id || ids.value
  getInventory(_id).then(response => {
    form.value = response.data;
    open.value = true;
    title.value = "修改库存";
  });
}

/** 提交按钮 */
function submitForm() {
  proxy.$refs["inventoryRef"].validate(valid => {
    if (valid) {
      if (form.value.id != null) {
        updateInventory(form.value).then(response => {
          proxy.$modal.msgSuccess("修改成功");
          open.value = false;
          getList();
        });
      } else {
        addInventory(form.value).then(response => {
          proxy.$modal.msgSuccess("新增成功");
          open.value = false;
          getList();
        });
      }
    }
  });
}

/** 删除按钮操作 */
function handleDelete(row) {
  const _ids = row.id || ids.value;
  proxy.$modal.confirm('是否确认删除库存编号为"' + _ids + '"的数据项？').then(function() {
    return delInventory(_ids);
  }).then(() => {
    getList();
    proxy.$modal.msgSuccess("删除成功");
  }).catch(() => {});
}

/** 低库存查询 */
function handleLowStock() {
    queryType.value = 'low';
    queryParams.value.pageNum = 1;
    getList();
}

/** 缺货查询 */
function handleOutOfStock() {
    queryType.value = 'out';
    queryParams.value.pageNum = 1;
    getList();
}

/** 补货建议 */
function handleRestockSuggestions() {
    getRestockSuggestions().then(response=>{
        restockList.value = response.data || [];
        restockOpen.value = true;
    })
}

getList();
</script>
