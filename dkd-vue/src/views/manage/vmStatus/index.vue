<template>
  <div class="app-container">
    <el-form :model="queryParams" ref="queryRef" :inline="true" v-show="showSearch" label-width="68px">
      <el-form-item label="设备编号" prop="innerCode">
        <el-input
          v-model="queryParams.innerCode"
          placeholder="请输入设备编号"
          clearable
          @keyup.enter="handleQuery"
        />
      </el-form-item>
     
      <el-form-item>
        <el-button type="primary" icon="Search" @click="handleQuery">搜索</el-button>
        <el-button icon="Refresh" @click="resetQuery">重置</el-button>
      </el-form-item>
    </el-form>
    <el-table v-loading="loading" :data="vmList" @selection-change="handleSelectionChange">
      <el-table-column label="序号" type="index" width="55" align="center" />
      <el-table-column label="设备编号" align="center" prop="innerCode" />
      <el-table-column label="设备型号" align="center" prop="vmTypeId" >
        <template #default="scope">
          <div v-for="item in vmTypeOptions" :key="item.id">
            <span v-if="item.id === scope.row.vmTypeId">{{ item.name }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="详细地址" align="left" prop="addr" show-overflow-tooltip="true"/>
      
     <el-table-column label="运营状态" align="center" prop="vmStatus">
        <template #default="scope">
          <dict-tag :options="vm_status" :value="scope.row.vmStatus" />
        </template>
      </el-table-column>
      <el-table-column label="设备状态" align="center" prop="vmStatus">
        <template #default="scope">
          <!-- 复用详情弹窗的容错函数：runningStatus 可能是非法 JSON，裸 JSON.parse 会炸整表 -->
          {{ runningStatusText(scope.row.runningStatus) }}
        </template>
      </el-table-column>
      <el-table-column label="操作" align="center" class-name="small-padding fixed-width">
        <template #default="scope">
          <el-button link type="primary"  @click="getVmInfo(scope.row)" v-hasPermi="['manage:vm:query']">查看详情</el-button>
          <!-- <el-button link type="primary"  @click="handleDelete(scope.row)" v-hasPermi="['manage:vm:remove']">删除</el-button> -->
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

    <!-- 设备详情对话框：数据来自 GET /manage/vm/{id}。
         后端只返回 nodeId/partnerId/regionId/vmTypeId 等裸 ID，名称必须用页面初始化时
         已加载的选项列表（vmTypeOptions/nodeOptions/partnerOptions/regionOptions）翻译；
         runningStatus 是后端存的 JSON 字符串，需容错解析后再判断，与列表列口径保持一致。 -->
    <el-dialog :title="title" v-model="open" width="700px" append-to-body>
      <el-descriptions :column="2" border size="default">
        <el-descriptions-item label="设备编号">{{ form.innerCode ?? '—' }}</el-descriptions-item>
        <el-descriptions-item label="设备型号">{{ optionName(vmTypeOptions, form.vmTypeId, 'name') }}</el-descriptions-item>
        <el-descriptions-item label="所属点位">{{ optionName(nodeOptions, form.nodeId, 'nodeName') }}</el-descriptions-item>
        <el-descriptions-item label="合作商">{{ optionName(partnerOptions, form.partnerId, 'partnerName') }}</el-descriptions-item>
        <el-descriptions-item label="所属区域">{{ optionName(regionOptions, form.regionId, 'regionName') }}</el-descriptions-item>
        <el-descriptions-item label="设备容量">{{ form.channelMaxCapacity ?? '—' }}</el-descriptions-item>
        <el-descriptions-item label="运营状态">
          <dict-tag :options="vm_status" :value="form.vmStatus" />
        </el-descriptions-item>
        <el-descriptions-item label="设备状态">
          <el-tag :type="runningStatusTag(form.runningStatus)" size="small">{{ runningStatusText(form.runningStatus) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="详细地址" :span="2">{{ form.addr ?? '—' }}</el-descriptions-item>
        <el-descriptions-item label="最近补货时间">{{ parseTime(form.lastSupplyTime) || '—' }}</el-descriptions-item>
        <el-descriptions-item label="更新时间">{{ form.updateTime ?? '—' }}</el-descriptions-item>
        <el-descriptions-item label="创建时间" :span="2">{{ form.createTime ?? '—' }}</el-descriptions-item>
      </el-descriptions>
      <template #footer>
        <div class="dialog-footer">
          <el-button @click="open = false">关 闭</el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup name="Vm">
import { listVm, getVm, delVm, addVm, updateVm } from "@/api/manage/vm";
import { listVmType } from "@/api/manage/vmType";
import { listPartner } from "@/api/manage/partner";
import { loadAllParams } from "@/api/page";
import { listNode } from "@/api/manage/node";
import { parseTime } from "@/utils/ruoyi";
import { listRegion } from "@/api/manage/region";

const { proxy } = getCurrentInstance();
const { vm_status } = proxy.useDict('vm_status');

const vmList = ref([]);
const open = ref(false);
const loading = ref(true);
const showSearch = ref(true);
const ids = ref([]);
const single = ref(true);
const multiple = ref(true);
const total = ref(0);
const title = ref("");

const data = reactive({
  form: {},
  queryParams: {
    pageNum: 1,
    pageSize: 10,
    innerCode: null,
    nodeId: null,
    businessType: null,
    regionId: null,
    partnerId: null,
    vmTypeId: null,
    vmStatus: null,
    runningStatus: null,
    policyId: null,
  },
  rules: {
    nodeId: [
      { required: true, message: "点位Id不能为空", trigger: "blur" }
    ],
    vmTypeId: [
      { required: true, message: "设备型号不能为空", trigger: "blur" }
    ],
  }
});

const { queryParams, form, rules } = toRefs(data);

/** 查询设备管理列表 */
function getList() {
  loading.value = true;
  listVm(queryParams.value).then(response => {
    vmList.value = response.rows;
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
    channelMaxCapacity: null,
    nodeId: null,
    addr: null,
    lastSupplyTime: null,
    businessType: null,
    regionId: null,
    partnerId: null,
    vmTypeId: null,
    vmStatus: null,
    runningStatus: null,
    longitudes: null,
    latitude: null,
    clientId: null,
    policyId: null,
    createTime: null,
    updateTime: null
  };
  proxy.resetForm("vmRef");
}

/** 搜索按钮操作 */
function handleQuery() {
  queryParams.value.pageNum = 1;
  getList();
}

/** 重置按钮操作 */
function resetQuery() {
  proxy.resetForm("queryRef");
  handleQuery();
}

// 多选框选中数据
function handleSelectionChange(selection) {
  ids.value = selection.map(item => item.id);
  single.value = selection.length != 1;
  multiple.value = !selection.length;
}

/** 新增按钮操作 */
function handleAdd() {
  reset();
  open.value = true;
  title.value = "添加设备管理";
}

/** 修改按钮操作 */
function getVmInfo(row) {
  reset();
  const _id = row.id || ids.value
  getVm(_id).then(response => {
    form.value = response.data;
    open.value = true;
    title.value = "设备详情";
  });
}

/**
 * 详情里把裸 ID 翻译成名称
 * 模板中传入的 optionList 是已自动解包的数组（不是 ref），因此这里直接当数组用。
 * 查不到（列表未加载完 / 数据被删）时回退为 '—'，不显示裸 ID 免得误导运维。
 */
function optionName(optionList, id, field) {
  if (id === null || id === undefined) {
    return '—';
  }
  const hit = Array.isArray(optionList) ? optionList.find(item => item.id === id) : null;
  return hit && hit[field] ? hit[field] : '—';
}

/**
 * runningStatus 是后端存库的 JSON 字符串（如 {"statusCode":"1001","status":true}），
 * 硬件上报或历史数据可能不是合法 JSON —— 解析失败按"异常"处理，与列表「设备状态」列口径一致，
 * 绝不能让 JSON.parse 抛错把整个详情弹窗打挂。
 */
function parseRunningStatus(raw) {
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch (e) {
    return null;
  }
}

function runningStatusText(raw) {
  const parsed = parseRunningStatus(raw);
  return parsed !== null && parsed.status === true ? '正常' : '异常';
}

function runningStatusTag(raw) {
  return runningStatusText(raw) === '正常' ? 'success' : 'danger';
}

/** 提交按钮 */
function submitForm() {
  proxy.$refs["vmRef"].validate(valid => {
    if (valid) {
      if (form.value.id != null) {
        updateVm(form.value).then(response => {
          proxy.$modal.msgSuccess("修改成功");
          open.value = false;
          getList();
        });
      } else {
        addVm(form.value).then(response => {
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
  proxy.$modal.confirm('是否确认删除设备管理编号为"' + _ids + '"的数据项？').then(function() {
    return delVm(_ids);
  }).then(() => {
    getList();
    proxy.$modal.msgSuccess("删除成功");
  }).catch(() => {});
}

/** 导出按钮操作 */
function handleExport() {
  proxy.download('manage/vm/export', {
    ...queryParams.value
  }, `vm_${new Date().getTime()}.xlsx`)
}

// 查询设备类型
const vmTypeOptions = ref([]);
const vmTypeList = () => {
  listVmType(loadAllParams).then(response => {
    vmTypeOptions.value = response.rows;
  });
};


// 查询合作商
const partnerOptions = ref([]);
const partnerList = () => {
  listPartner(loadAllParams).then(response => {
    partnerOptions.value = response.rows;
  });
};
// 查询点位
const nodeOptions = ref([]);
const nodeList = () => {
  listNode(loadAllParams).then(response => {
    nodeOptions.value = response.rows;
  });
};
// 查询区域
const regionOptions = ref([]);
const regionList = () => {
  listRegion(loadAllParams).then(response => {
    regionOptions.value = response.rows;
  });
};

nodeList();
vmTypeList();
partnerList();
regionList();
getList();
</script>
