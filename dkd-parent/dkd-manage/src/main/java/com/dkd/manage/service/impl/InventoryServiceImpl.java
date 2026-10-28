package com.dkd.manage.service.impl;

import java.util.ArrayList;
import java.util.Date;
import java.util.List;

import com.dkd.common.exception.ServiceException;
import com.dkd.common.utils.DateUtils;
import com.dkd.common.utils.SecurityUtils;
import com.dkd.manage.domain.Channel;
import com.dkd.manage.domain.InventoryLog;
import com.dkd.manage.domain.Sku;
import com.dkd.manage.domain.VendingMachine;
import com.dkd.manage.domain.dto.InventoryAlertDto;
import com.dkd.manage.domain.dto.RestockSuggestionDto;
import com.dkd.manage.mapper.ChannelMapper;
import com.dkd.manage.mapper.InventoryLogMapper;
import com.dkd.manage.mapper.SkuMapper;
import com.dkd.manage.mapper.VendingMachineMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import com.dkd.manage.mapper.InventoryMapper;
import com.dkd.manage.domain.Inventory;
import com.dkd.manage.service.IInventoryService;

/**
 * 库存Service业务层处理
 *
 * @author ruoyi
 * @date 2025-01-08
 */
@Service
public class InventoryServiceImpl implements IInventoryService
{
    private static final Logger log = LoggerFactory.getLogger(InventoryServiceImpl.class);

    @Autowired
    private InventoryMapper inventoryMapper;

    @Autowired
    private InventoryLogMapper inventoryLogMapper;

    /** P1-15：显示字段反查所需（原先直接注入在 Controller 里，随逻辑一并下沉） */
    @Autowired
    private VendingMachineMapper vendingMachineMapper;

    @Autowired
    private SkuMapper skuMapper;

    @Autowired
    private ChannelMapper channelMapper;

    /**
     * 查询库存
     *
     * @param id 库存主键
     * @return 库存
     */
    @Override
    public Inventory selectInventoryById(Long id)
    {
        return inventoryMapper.selectInventoryById(id);
    }

    /**
     * 查询库存列表
     *
     * @param inventory 库存
     * @return 库存
     */
    @Override
    public List<Inventory> selectInventoryList(Inventory inventory)
    {
        return inventoryMapper.selectInventoryList(inventory);
    }

    /**
     * 新增库存（库存行 + 初始化流水，同一事务）
     *
     * <p>新增库存等价于"初始化"：按清晰版 §4.3 必须写 change_type=4（调整，含初始化）流水，
     * business_type=INIT；否则会出现"台账有行、审计无记录"的库存。
     *
     * @param inventory 库存
     * @return 结果
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public int insertInventory(Inventory inventory)
    {
        if (inventory == null)
        {
            throw new ServiceException("新增库存失败：参数不能为空");
        }
        // 检查是否已存在库存记录（货道优先、旧唯一键回退，与库存变更服务同一口径）
        Inventory existInventory = locateInventory(inventory.getVmId(), inventory.getSkuId(), inventory.getChannelId());
        if (existInventory != null)
        {
            throw new ServiceException("该售货机货道已存在该商品的库存记录，请使用修改操作");
        }

        Date now = DateUtils.getNowDate();
        inventory.setCreateTime(now);
        inventory.setUpdateTime(now);
        int result = inventoryMapper.insertInventory(inventory);
        if (result <= 0)
        {
            throw new ServiceException("新增库存失败：库存记录写入失败");
        }

        int quantity = inventory.getQuantity() == null ? 0 : inventory.getQuantity();
        // 通用插入语句不含 is_alert，按清晰版口径补写预警标记
        inventoryMapper.updateInventoryStockState(inventory.getId(), quantity,
                buildAlertFlag(quantity, inventory.getMinQuantity()), null, now);
        // 初始化流水（change_type=4 / business_type=INIT）
        writeInventoryLog(inventory.getVmId(), inventory.getSkuId(), inventory.getChannelId(),
                quantity, 0, quantity, InventoryLog.CHANGE_TYPE_ADJUST, InventoryLog.BUSINESS_TYPE_INIT,
                resolveCurrentOperator(), now);
        return result;
    }

    /**
     * 新增库存（支持显示字段反查 ID，P1-15 从 Controller 下沉）。
     *
     * <p>为什么下沉：原 InventoryController.add() 里有三段「显示字段→ID 反查」+四段参数校验，
     * Controller 直接注入三个 Mapper 做业务逻辑，违反「Controller 只做参数校验 + 调 Service」
     * 的分层规范。反查/校验失败抛 {@link ServiceException}，由全局异常处理器转成
     * {@code AjaxResult.error(msg)}——错误文案与原 Controller 版逐字一致，前端无感知。
     *
     * @param inventory 库存（可用 innerCode/skuName/channelCode+innerCode 代替对应 ID）
     * @return 结果
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public int insertInventoryByDisplay(Inventory inventory)
    {
        if (inventory == null)
        {
            throw new ServiceException("库存数量不能为空");
        }

        // 三段「显示字段 → ID」反查（与原 Controller 逻辑逐字对齐）
        if (inventory.getVmId() == null && inventory.getInnerCode() != null && !inventory.getInnerCode().isEmpty())
        {
            VendingMachine vm = vendingMachineMapper.selectVendingMachineByInnerCode(inventory.getInnerCode());
            if (vm == null)
            {
                throw new ServiceException("售货机编号【" + inventory.getInnerCode() + "】不存在");
            }
            inventory.setVmId(vm.getId());
        }

        if (inventory.getSkuId() == null && inventory.getSkuName() != null && !inventory.getSkuName().isEmpty())
        {
            Sku sku = skuMapper.selectSkuBySkuName(inventory.getSkuName());
            if (sku == null)
            {
                throw new ServiceException("商品名称【" + inventory.getSkuName() + "】不存在");
            }
            inventory.setSkuId(sku.getSkuId());
        }

        if (inventory.getChannelId() == null && inventory.getInnerCode() != null && !inventory.getInnerCode().isEmpty()
            && inventory.getChannelCode() != null && !inventory.getChannelCode().isEmpty())
        {
            Channel channel = channelMapper.getChannelInfo(inventory.getInnerCode(), inventory.getChannelCode());
            if (channel == null)
            {
                throw new ServiceException("售货机【" + inventory.getInnerCode() + "】的货道【" + inventory.getChannelCode() + "】不存在");
            }
            inventory.setChannelId(channel.getId());
        }

        // 参数校验（文案与原 Controller 逐字一致）
        if (inventory.getVmId() == null)
        {
            throw new ServiceException("售货机ID不能为空");
        }
        if (inventory.getSkuId() == null)
        {
            throw new ServiceException("商品ID不能为空");
        }
        if (inventory.getChannelId() == null)
        {
            throw new ServiceException("货道ID不能为空");
        }
        if (inventory.getQuantity() == null)
        {
            throw new ServiceException("库存数量不能为空");
        }

        return insertInventory(inventory);
    }

    /**
     * 修改库存（阈值/档案类字段直接更新；数量变更转交库存变更流水）
     *
     * <p>为什么把 quantity 从通用更新里摘出来：current_stock 是库存数量事实，按清晰版 §4.3
     * 必须经"校验 → 台账 → 流水"的唯一入口；否则会出现"数量变了但审计没有记录"。
     * 后台库存编辑（PUT /manage/inventory）传 quantity 时按 change_type=4（调整）落流水。
     *
     * @param inventory 库存
     * @return 结果
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public int updateInventory(Inventory inventory)
    {
        if (inventory == null || inventory.getId() == null)
        {
            throw new ServiceException("修改库存失败：库存主键不能为空");
        }

        // 1) 数量从通用更新中摘出，其余字段（预警阈值、补货时间等）照常更新
        Integer targetQuantity = inventory.getQuantity();
        inventory.setQuantity(null);
        inventory.setUpdateTime(DateUtils.getNowDate());
        int result = inventoryMapper.updateInventory(inventory);

        if (targetQuantity == null)
        {
            return result;
        }

        // 2) 数量差异走统一入口（范围校验 + 重算 is_alert + 写调整流水）
        Inventory current = inventoryMapper.selectInventoryById(inventory.getId());
        if (current == null)
        {
            throw new ServiceException("修改库存失败：库存记录不存在，id=" + inventory.getId());
        }
        int beforeQuantity = current.getQuantity() == null ? 0 : current.getQuantity();
        if (targetQuantity.intValue() != beforeQuantity)
        {
            applyInventoryChange(current, targetQuantity.intValue() - beforeQuantity,
                    InventoryLog.CHANGE_TYPE_ADJUST, resolveCurrentOperator());
        }
        return result;
    }

    /**
     * 批量删除库存
     *
     * @param ids 需要删除的库存主键
     * @return 结果
     */
    @Override
    public int deleteInventoryByIds(Long[] ids)
    {
        return inventoryMapper.deleteInventoryByIds(ids);
    }

    /**
     * 删除库存信息
     *
     * @param id 库存主键
     * @return 结果
     */
    @Override
    public int deleteInventoryById(Long id)
    {
        return inventoryMapper.deleteInventoryById(id);
    }

    /**
     * 根据售货机ID、商品ID、货道ID查询库存
     *
     * @param vmId 售货机ID
     * @param skuId 商品ID
     * @param channelId 货道ID
     * @return 库存信息
     */
    @Override
    public Inventory selectByVmIdAndSkuIdAndChannelId(Long vmId, Long skuId, Long channelId)
    {
        return inventoryMapper.selectByVmIdAndSkuIdAndChannelId(vmId, skuId, channelId);
    }

    /**
     * 根据售货机ID、货道ID查询库存
     *
     * @param vmId 售货机ID
     * @param channelId 货道ID
     * @return 库存信息
     */
    @Override
    public Inventory selectByVmIdAndChannelId(Long vmId, Long channelId)
    {
        return inventoryMapper.selectByVmIdAndChannelId(vmId, channelId);
    }

    /**
     * 查询低库存预警列表
     *
     * @return 低库存列表
     */
    @Override
    public List<Inventory> selectLowInventoryList()
    {
        return inventoryMapper.selectLowInventoryList();
    }

    /**
     * 查询缺货库存列表
     *
     * @return 缺货列表
     */
    @Override
    public List<Inventory> selectOutOfStockList()
    {
        return inventoryMapper.selectOutOfStockList();
    }

    /**
     * 批量插入库存
     *
     * @param inventoryList 库存列表
     * @return 结果
     */
    @Override
    public int batchInsertInventory(List<Inventory> inventoryList)
    {
        if (inventoryList == null || inventoryList.isEmpty())
        {
            return 0;
        }
        Date now = DateUtils.getNowDate();
        for (Inventory inventory : inventoryList)
        {
            inventory.setCreateTime(now);
        }
        return inventoryMapper.batchInsertInventory(inventoryList);
    }

    /**
     * 更新库存数量（库存变更的唯一入口：校验 → 更新台账 → 写流水，同一事务）
     *
     * <p>清晰版口径（docs/DKD业务规范-清晰版.md §4.3）：
     * <ul>
     *   <li>查询按 (vmId, channelId) 优先（库存粒度以货道为准），未命中回退 (vmId, skuId)——兼容旧唯一键 uk_vm_sku；</li>
     *   <li>after 必须 &gt;= 0；after 必须 &lt;= max_stock（max 为空不校验）；补货类型按剩余容量夹取而不是拒绝；</li>
     *   <li>每次变更重算 is_alert = (after &lt;= min_stock)，补货同时更新 last_supply_time；</li>
     *   <li>流水写真实列，change_amount 记"实际变动量"（夹取后的值），不是请求量。</li>
     * </ul>
     *
     * @param vmId 售货机ID
     * @param skuId 商品ID（channelId 未命中时用于旧唯一键回退）
     * @param channelId 货道ID（优先按它定位台账）
     * @param changeQuantity 变动数量（正数为增加，负数为减少）
     * @param changeType 变动类型：1-销售 2-补货 3-损耗 4-调整 5-盘点（见 InventoryLog.CHANGE_TYPE_*）
     * @param operator 操作人姓名（写 operator_name）
     * @return 结果
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public int updateInventoryQuantity(Long vmId, Long skuId, Long channelId, Integer changeQuantity, Integer changeType, String operator)
    {
        if (vmId == null)
        {
            throw new ServiceException("库存变更失败：售货机ID不能为空");
        }
        if (channelId == null && skuId == null)
        {
            throw new ServiceException("库存变更失败：货道ID与商品ID不能同时为空");
        }
        if (changeType == null || !isValidChangeType(changeType))
        {
            throw new ServiceException("库存变更失败：变动类型不合法");
        }
        if (changeQuantity == null || changeQuantity == 0)
        {
            return 0;
        }

        // 1) 定位台账：货道优先，未命中再按旧唯一键 (vm_id, sku_id) 回退
        Inventory inventory = locateInventory(vmId, skuId, channelId);
        if (inventory == null)
        {
            throw new ServiceException("库存信息不存在，请先初始化库存（vmId=" + vmId + "，channelId=" + channelId + "，skuId=" + skuId + "）");
        }

        // 2)~4) 统一变更逻辑：范围校验 → 更新台账 → 写流水
        return applyInventoryChange(inventory, changeQuantity.intValue(), changeType, operator);
    }

    /**
     * 库存变更的统一实现（校验 → 更新台账 → 写流水），供数量接口、后台编辑、初始化共用。
     *
     * <p>为什么抽成私有方法：清晰版 §4.3 要求"库存变更只能经唯一入口"，
     * 后台编辑（updateInventory）与数量接口（updateInventoryQuantity）必须走同一份校验与流水逻辑，
     * 否则两条路径的 before/after 与 is_alert 口径会分叉。
     *
     * @param inventory 已定位到的台账行
     * @param changeQuantity 变动数量（正数增加/负数减少）
     * @param changeType 变动类型（1~5）
     * @param operator 操作人姓名
     * @return 更新的行数；无实际变化时返回 0
     */
    private int applyInventoryChange(Inventory inventory, int changeQuantity, Integer changeType, String operator)
    {
        if (inventory == null || inventory.getId() == null)
        {
            throw new ServiceException("库存变更失败：库存记录不存在");
        }

        // 计算并夹取变更后库存。用 long 中间量避免 before + change 的 int 溢出
        int beforeStock = inventory.getQuantity() == null ? 0 : inventory.getQuantity();
        long targetStock = (long) beforeStock + changeQuantity;
        if (targetStock < 0)
        {
            throw new ServiceException("库存不足，当前库存：" + beforeStock + "，变动数量：" + changeQuantity);
        }
        Integer maxStock = inventory.getMaxQuantity();
        boolean restock = InventoryLog.CHANGE_TYPE_RESTOCK.equals(changeType);
        if (maxStock != null && targetStock > maxStock)
        {
            if (!restock)
            {
                throw new ServiceException("库存超过最大容量，最大容量：" + maxStock + "，变动后库存：" + targetStock);
            }
            // 补货按剩余容量夹取（清晰版 §2.2：不允许超过容量，流水记实际数量）
            targetStock = maxStock;
        }
        if (targetStock > Integer.MAX_VALUE)
        {
            throw new ServiceException("库存变更失败：变动后库存超出可表示范围");
        }
        int afterStock = (int) targetStock;
        int actualChange = afterStock - beforeStock;
        if (actualChange == 0)
        {
            // 夹取后没有实际变化（例如已满容量仍补货）：不写 change_amount=0 的流水，避免审计噪声
            return 0;
        }

        // 3) 更新台账数量事实（current_stock / is_alert / last_supply_time）
        Date now = DateUtils.getNowDate();
        Integer isAlert = buildAlertFlag(afterStock, inventory.getMinQuantity());
        Date lastSupplyTime = restock ? now : null;
        int result = inventoryMapper.updateInventoryStockState(inventory.getId(), afterStock, isAlert, lastSupplyTime, now);
        if (result <= 0)
        {
            throw new ServiceException("库存更新失败，库存ID：" + inventory.getId());
        }

        // 4) 写流水（真实列）；change_amount 记实际变动量，操作人写 operator_name
        writeInventoryLog(inventory.getVmId(), inventory.getSkuId(), inventory.getChannelId(),
                actualChange, beforeStock, afterStock, changeType, buildBusinessType(changeType),
                operator, now);
        return result;
    }

    /**
     * 初始化库存（用于新设备或新商品）
     *
     * <p>初始化也属于库存变更：必须经本服务写流水，change_type=4（调整含初始化）、
     * business_type=INIT、operator_name="系统"（清晰版 §4.3）。
     *
     * @param vmId 售货机ID
     * @param skuId 商品ID
     * @param channelId 货道ID
     * @param quantity 初始库存数量
     * @param minQuantity 最小库存预警值
     * @param maxQuantity 最大库存容量
     * @return 结果
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public int initInventory(Long vmId, Long skuId, Long channelId, Integer quantity, Integer minQuantity, Integer maxQuantity)
    {
        if (vmId == null || channelId == null || skuId == null)
        {
            throw new ServiceException("初始化库存失败：售货机ID、商品ID、货道ID都不能为空");
        }
        if (quantity == null || quantity < 0)
        {
            throw new ServiceException("初始化库存失败：初始库存数量不能为空且不能为负数");
        }
        if (maxQuantity != null && quantity > maxQuantity)
        {
            throw new ServiceException("初始化库存失败：初始库存：" + quantity + " 超过最大容量：" + maxQuantity);
        }

        // 判重口径与 updateInventoryQuantity 一致：货道优先，旧唯一键回退
        Inventory existInventory = inventoryMapper.selectByVmIdAndChannelId(vmId, channelId);
        if (existInventory == null)
        {
            existInventory = inventoryMapper.selectByVmIdAndSkuId(vmId, skuId);
        }
        if (existInventory != null)
        {
            throw new ServiceException("库存记录已存在，请使用更新操作");
        }

        Date now = DateUtils.getNowDate();

        // 创建库存记录
        Inventory inventory = new Inventory();
        inventory.setVmId(vmId);
        inventory.setSkuId(skuId);
        inventory.setChannelId(channelId);
        inventory.setQuantity(quantity);
        inventory.setMinQuantity(minQuantity);
        inventory.setMaxQuantity(maxQuantity);
        inventory.setLastSupplyTime(now);
        inventory.setCreateTime(now);
        inventory.setUpdateTime(now);

        int result = inventoryMapper.insertInventory(inventory);
        if (result <= 0)
        {
            throw new ServiceException("初始化库存失败：库存记录写入失败");
        }

        // 通用插入语句不含 is_alert，落库后按清晰版口径补写预警标记
        inventoryMapper.updateInventoryStockState(inventory.getId(), quantity, buildAlertFlag(quantity, minQuantity), null, now);

        // 记录初始化流水：change_type=4（调整，含初始化），business_type=INIT，操作人=系统
        writeInventoryLog(vmId, skuId, channelId, quantity, 0, quantity,
                InventoryLog.CHANGE_TYPE_ADJUST, InventoryLog.BUSINESS_TYPE_INIT,
                InventoryLog.OPERATOR_SYSTEM, now);

        return result;
    }

    /**
     * 定位库存台账：货道优先（库存粒度以货道为准），未命中回退旧唯一键 (vm_id, sku_id)。
     *
     * <p>为什么要有回退：Phase B 之前 tb_inventory 的唯一键仍是 uk_vm_sku (vm_id, sku_id)，
     * 按货道只可能是历史数据里的"一 sku 多货道"共用一个库存行（清晰版 §8-1 待决策）。
     *
     * @param vmId 售货机ID
     * @param skuId 商品ID
     * @param channelId 货道ID
     * @return 库存台账；不存在返回 null
     */
    private Inventory locateInventory(Long vmId, Long skuId, Long channelId)
    {
        if (vmId == null)
        {
            return null;
        }
        Inventory inventory = null;
        if (channelId != null)
        {
            inventory = inventoryMapper.selectByVmIdAndChannelId(vmId, channelId);
        }
        if (inventory == null && skuId != null)
        {
            inventory = inventoryMapper.selectByVmIdAndSkuId(vmId, skuId);
        }
        return inventory;
    }

    /**
     * 写库存流水（真实列），库存变更的唯一审计出口。
     *
     * @param vmId 售货机ID
     * @param skuId 商品ID
     * @param channelId 货道ID
     * @param changeAmount 变动数量（正数增加/负数减少，记实际值）
     * @param beforeStock 变动前库存
     * @param afterStock 变动后库存
     * @param changeType 变动类型（1~5）
     * @param businessType 业务类型字符串（SALE/RESTOCK/LOSS/ADJUST/INIT 等）
     * @param operator 操作人姓名
     * @param createTime 流水时间
     */
    private void writeInventoryLog(Long vmId, Long skuId, Long channelId, int changeAmount, int beforeStock,
                                   int afterStock, Integer changeType, String businessType, String operator,
                                   Date createTime)
    {
        InventoryLog inventoryLog = new InventoryLog();
        inventoryLog.setVmId(vmId);
        inventoryLog.setSkuId(skuId);
        inventoryLog.setChannelId(channelId);
        inventoryLog.setChangeType(changeType);
        inventoryLog.setChangeAmount(changeAmount);
        inventoryLog.setBeforeStock(beforeStock);
        inventoryLog.setAfterStock(afterStock);
        inventoryLog.setBusinessType(businessType);
        inventoryLog.setOperatorName(operator);
        inventoryLog.setCreateTime(createTime);
        inventoryLogMapper.insertInventoryLog(inventoryLog);
    }

    /**
     * 取当前登录人账号作为流水操作人；无认证上下文（定时任务/内部调用）时记"系统"。
     *
     * <p>为什么允许降级而不是抛错：后台库存编辑接口必然有登录人；而定时对账等内部调用
     * 没有 SecurityContext，"取不到登录人"不是业务失败，不应把一次合法变更变成异常。
     *
     * @return 操作人；取不到时返回"系统"
     */
    private String resolveCurrentOperator()
    {
        try
        {
            String username = SecurityUtils.getUsername();
            if (username != null && !username.isEmpty())
            {
                return username;
            }
            return InventoryLog.OPERATOR_SYSTEM;
        }
        catch (Exception e)
        {
            log.debug("无认证上下文，库存流水操作人按系统记录：{}", e.getMessage());
            return InventoryLog.OPERATOR_SYSTEM;
        }
    }

    /**
     * 校验变动类型是否在清晰版 §4.3 的白名单内（1~5），类型非法必须拒绝而不是静默放行。
     *
     * @param changeType 变动类型
     * @return 是否合法
     */
    private boolean isValidChangeType(Integer changeType)
    {
        if (changeType == null)
        {
            return false;
        }
        return changeType.intValue() >= InventoryLog.CHANGE_TYPE_SALE.intValue()
                && changeType.intValue() <= InventoryLog.CHANGE_TYPE_STOCKTAKE.intValue();
    }

    /**
     * 计算预警标记 is_alert = (current_stock &lt;= min_stock)（清晰版 §4.3）。
     *
     * <p>min_stock 为空时按 0 处理：current_stock=0 必须预警，否则"缺货"不会被提醒。
     *
     * @param currentStock 变更后库存
     * @param minStock 预警阈值
     * @return 1-预警 0-正常
     */
    private Integer buildAlertFlag(Integer currentStock, Integer minStock)
    {
        if (currentStock == null)
        {
            return 0;
        }
        int threshold = minStock == null ? 0 : minStock;
        return currentStock <= threshold ? 1 : 0;
    }

    /**
     * 把变动类型映射为流水 business_type 字符串（清晰版 §2.2 / §4.3）。
     *
     * @param changeType 变动类型
     * @return 业务类型字符串
     */
    private String buildBusinessType(Integer changeType)
    {
        if (InventoryLog.CHANGE_TYPE_SALE.equals(changeType))
        {
            return InventoryLog.BUSINESS_TYPE_SALE;
        }
        if (InventoryLog.CHANGE_TYPE_RESTOCK.equals(changeType))
        {
            return InventoryLog.BUSINESS_TYPE_RESTOCK;
        }
        if (InventoryLog.CHANGE_TYPE_LOSS.equals(changeType))
        {
            return InventoryLog.BUSINESS_TYPE_LOSS;
        }
        if (InventoryLog.CHANGE_TYPE_STOCKTAKE.equals(changeType))
        {
            return InventoryLog.BUSINESS_TYPE_STOCKTAKE;
        }
        return InventoryLog.BUSINESS_TYPE_ADJUST;
    }

    /**
     * 生成库存预警列表
     *
     * <p>P1-11：候选加载收敛到 {@link #loadAlertCandidates()}（单条合并 SQL），
     * 本方法只做 DTO 组装；{@code checkInventoryAlert} 按 quantity==0 优先分类，
     * 与合并 SQL 的口径天然一致。
     *
     * @return 预警列表
     */
    @Override
    @Transactional(readOnly = true)
    public List<InventoryAlertDto> generateInventoryAlerts()
    {
        List<InventoryAlertDto> alerts = new ArrayList<>();
        for (Inventory inventory : loadAlertCandidates())
        {
            if (inventory == null)
            {
                continue;
            }
            InventoryAlertDto alert = checkInventoryAlert(inventory);
            if (alert != null)
            {
                alerts.add(alert);
            }
        }
        return alerts;
    }

    /**
     * 检查库存并发送预警
     *
     * @param inventory 库存信息
     * @return 预警DTO，如果没有预警则返回null
     */
    @Override
    public InventoryAlertDto checkInventoryAlert(Inventory inventory)
    {
        if (inventory == null || inventory.getQuantity() == null)
        {
            return null;
        }

        Integer quantity = inventory.getQuantity();
        Integer minQuantity = inventory.getMinQuantity();

        // 缺货预警
        if (quantity == 0)
        {
            InventoryAlertDto alert = new InventoryAlertDto();
            alert.setAlertType(2); // 缺货预警
            alert.setAlertLevel(3); // 严重
            alert.setInventory(inventory);
            alert.setAlertMessage("【严重】售货机【" + inventory.getInnerCode() + "】的商品【" + inventory.getSkuName() + "】已缺货");
            alert.setSuggestion("请立即安排补货，避免影响销售");
            return alert;
        }

        // 低库存预警
        if (minQuantity != null && quantity <= minQuantity)
        {
            InventoryAlertDto alert = new InventoryAlertDto();
            alert.setAlertType(1); // 低库存预警

            // 计算预警级别
            double ratio = (double) quantity / minQuantity;
            if (ratio <= 0.5)
            {
                alert.setAlertLevel(3); // 严重：库存低于预警值的50%
                alert.setAlertMessage("【严重】售货机【" + inventory.getInnerCode() + "】的商品【" + inventory.getSkuName() + "】库存严重不足，当前库存：" + quantity);
                alert.setSuggestion("请尽快安排补货");
            }
            else if (ratio <= 0.8)
            {
                alert.setAlertLevel(2); // 紧急：库存低于预警值的80%
                alert.setAlertMessage("【紧急】售货机【" + inventory.getInnerCode() + "】的商品【" + inventory.getSkuName() + "】库存偏低，当前库存：" + quantity);
                alert.setSuggestion("建议尽快补货");
            }
            else
            {
                alert.setAlertLevel(1); // 普通：库存达到预警值
                alert.setAlertMessage("【提醒】售货机【" + inventory.getInnerCode() + "】的商品【" + inventory.getSkuName() + "】库存达到预警值，当前库存：" + quantity);
                alert.setSuggestion("建议及时补货");
            }

            alert.setInventory(inventory);
            return alert;
        }

        return null;
    }

    /**
     * 生成补货建议列表
     *
     * <p>P1-11：候选加载收敛到 {@link #loadAlertCandidates()}（单条合并 SQL），
     * 与 {@link #generateInventoryAlerts()} 共用同一段加载逻辑（原先两段几乎逐行重复）；
     * 本方法只做建议计算，优先级仍由 {@code generateRestockSuggestion} 统一计算。
     *
     * @return 补货建议列表
     */
    @Override
    @Transactional(readOnly = true)
    public List<RestockSuggestionDto> generateRestockSuggestions()
    {
        List<RestockSuggestionDto> suggestions = new ArrayList<>();
        for (Inventory inventory : loadAlertCandidates())
        {
            if (inventory == null)
            {
                continue;
            }
            RestockSuggestionDto suggestion = generateRestockSuggestion(inventory);
            if (suggestion != null)
            {
                suggestions.add(suggestion);
            }
        }
        return suggestions;
    }

    /**
     * 加载预警候选（P1-11）：低库存 ∪ 缺货，单条合并 SQL 天然去重。
     *
     * <p>原先是「selectLowInventoryList + selectOutOfStockList」两次无条件全表查询
     * 加内存 seenStockIds 去重——两接口同开一次就是 4 次全表扫描；合并后各 1 次，
     * 且去重根因（两段口径在 current_stock=0 处重叠）被 SQL 层消除。
     *
     * @return 预警候选列表（current_stock 升序，缺货在前）
     */
    private List<Inventory> loadAlertCandidates()
    {
        List<Inventory> candidates = inventoryMapper.selectAlertCandidates();
        return candidates != null ? candidates : new ArrayList<>();
    }

    /**
     * 为指定库存生成补货建议
     *
     * @param inventory 库存信息
     * @return 补货建议
     */
    @Override
    public RestockSuggestionDto generateRestockSuggestion(Inventory inventory)
    {
        if (inventory == null || inventory.getMaxQuantity() == null)
        {
            return null;
        }

        Integer currentQuantity = inventory.getQuantity();
        Integer maxQuantity = inventory.getMaxQuantity();

        // 如果库存已经达到最大容量的90%，不需要补货
        if (currentQuantity >= maxQuantity * 0.9)
        {
            return null;
        }

        RestockSuggestionDto suggestion = new RestockSuggestionDto();
        suggestion.setVmId(inventory.getVmId());
        suggestion.setInnerCode(inventory.getInnerCode());
        suggestion.setSkuId(inventory.getSkuId());
        suggestion.setSkuName(inventory.getSkuName());
        suggestion.setChannelId(inventory.getChannelId());
        suggestion.setChannelCode(inventory.getChannelCode());
        suggestion.setCurrentQuantity(currentQuantity);
        suggestion.setMaxCapacity(maxQuantity);

        // 计算建议补货数量（补到最大容量的80%-90%，留10%-20%的安全空间）
        Integer targetQuantity = (int) (maxQuantity * 0.85);
        Integer suggestedQuantity = targetQuantity - currentQuantity;

        // 至少补货到最小预警值的2倍
        if (inventory.getMinQuantity() != null)
        {
            Integer minRestock = inventory.getMinQuantity() * 2 - currentQuantity;
            if (minRestock > suggestedQuantity)
            {
                suggestedQuantity = minRestock;
            }
        }

        // 确保不超过最大容量
        if (currentQuantity + suggestedQuantity > maxQuantity)
        {
            suggestedQuantity = maxQuantity - currentQuantity;
        }

        suggestion.setSuggestedQuantity(suggestedQuantity);
        suggestion.setAfterRestockQuantity(currentQuantity + suggestedQuantity);

        // 计算优先级
        if (currentQuantity == 0)
        {
            suggestion.setPriority(4); // 紧急：缺货
            suggestion.setReason("商品已缺货，需要立即补货");
            suggestion.setEstimatedDays(0);
        }
        else if (inventory.getMinQuantity() != null && currentQuantity <= inventory.getMinQuantity() * 0.5)
        {
            suggestion.setPriority(3); // 高：库存严重不足
            suggestion.setReason("库存低于预警值的50%，建议尽快补货");
            suggestion.setEstimatedDays(1);
        }
        else if (inventory.getMinQuantity() != null && currentQuantity <= inventory.getMinQuantity())
        {
            suggestion.setPriority(2); // 中：库存偏低
            suggestion.setReason("库存达到预警值，建议及时补货");
            suggestion.setEstimatedDays(2);
        }
        else
        {
            suggestion.setPriority(1); // 低：常规补货
            suggestion.setReason("常规补货，保持合理库存水平");
            suggestion.setEstimatedDays(3);
        }

        return suggestion;
    }
}
