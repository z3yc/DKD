package com.dkd.manage.controller;

import java.util.List;
import javax.servlet.http.HttpServletResponse;

import com.dkd.manage.domain.dto.InventoryAlertDto;
import com.dkd.manage.domain.dto.RestockSuggestionDto;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import com.dkd.common.annotation.Log;
import com.dkd.common.core.controller.BaseController;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.common.enums.BusinessType;
import com.dkd.manage.domain.Inventory;
import com.dkd.manage.service.IInventoryService;
import com.dkd.common.utils.poi.ExcelUtil;
import com.dkd.common.core.page.TableDataInfo;

/**
 * 库存Controller
 *
 * @author ruoyi
 * @date 2025-01-08
 */
@RestController
@RequestMapping("/manage/inventory")
public class InventoryController extends BaseController
{
    @Autowired
    private IInventoryService inventoryService;

    /**
     * 查询库存列表
     */
    @PreAuthorize("@ss.hasPermi('manage:inventory:list')")
    @GetMapping("/list")
    public TableDataInfo list(Inventory inventory)
    {
        startPage();
        List<Inventory> list = inventoryService.selectInventoryList(inventory);
        return getDataTable(list);
    }

    /**
     * 导出库存列表
     */
    @PreAuthorize("@ss.hasPermi('manage:inventory:export')")
    @Log(title = "库存", businessType = BusinessType.EXPORT)
    @PostMapping("/export")
    public void export(HttpServletResponse response, Inventory inventory)
    {
        List<Inventory> list = inventoryService.selectInventoryList(inventory);
        ExcelUtil<Inventory> util = new ExcelUtil<Inventory>(Inventory.class);
        util.exportExcel(response, list, "库存数据");
    }

    /**
     * 获取库存详细信息
     */
    @PreAuthorize("@ss.hasPermi('manage:inventory:query')")
    @GetMapping(value = "/{id}")
    public AjaxResult getInfo(@PathVariable("id") Long id)
    {
        return success(inventoryService.selectInventoryById(id));
    }

    /**
     * 新增库存
     *
     * <p>P1-15：显示字段反查 ID 与参数校验已下沉到
     * {@code IInventoryService.insertInventoryByDisplay}（反查失败抛 ServiceException，
     * 文案与本方法旧版逐字一致）。
     */
    @PreAuthorize("@ss.hasPermi('manage:inventory:add')")
    @Log(title = "库存", businessType = BusinessType.INSERT)
    @PostMapping
    public AjaxResult add(@RequestBody Inventory inventory)
    {
        return toAjax(inventoryService.insertInventoryByDisplay(inventory));
    }

    /**
     * 修改库存
     */
    @PreAuthorize("@ss.hasPermi('manage:inventory:edit')")
    @Log(title = "库存", businessType = BusinessType.UPDATE)
    @PutMapping
    public AjaxResult edit(@RequestBody Inventory inventory)
    {
        return toAjax(inventoryService.updateInventory(inventory));
    }

    /**
     * 删除库存
     */
    @PreAuthorize("@ss.hasPermi('manage:inventory:remove')")
    @Log(title = "库存", businessType = BusinessType.DELETE)
    @DeleteMapping("/{ids}")
    public AjaxResult remove(@PathVariable Long[] ids)
    {
        return toAjax(inventoryService.deleteInventoryByIds(ids));
    }

    /**
     * 查询低库存预警列表
     */
    @PreAuthorize("@ss.hasPermi('manage:inventory:list')")
    @GetMapping("/low")
    public TableDataInfo getLowInventoryList()
    {
        startPage();
        List<Inventory> list = inventoryService.selectLowInventoryList();
        return getDataTable(list);
    }

    /**
     * 查询缺货库存列表
     */
    @PreAuthorize("@ss.hasPermi('manage:inventory:list')")
    @GetMapping("/outofstock")
    public TableDataInfo getOutOfStockList()
    {
        startPage();
        List<Inventory> list = inventoryService.selectOutOfStockList();
        return getDataTable(list);
    }

    /**
     * 初始化库存
     */
    @PreAuthorize("@ss.hasPermi('manage:inventory:add')")
    @Log(title = "初始化库存", businessType = BusinessType.INSERT)
    @PostMapping("/init")
    public AjaxResult initInventory(@RequestBody Inventory inventory)
    {
        return toAjax(inventoryService.initInventory(
            inventory.getVmId(),
            inventory.getSkuId(),
            inventory.getChannelId(),
            inventory.getQuantity(),
            inventory.getMinQuantity(),
            inventory.getMaxQuantity()
        ));
    }

    /**
     * 获取库存预警列表
     *
     * <p>P1-11（破坏性变更）：返回结构由 AjaxResult 改为 TableDataInfo 并接入分页。
     * 前端唯一消费方为 dkd-vue（getInventoryAlerts 当前无页面引用）；
     * dkd-agent 只读 tb_* 表不走此 REST 接口。
     */
    @PreAuthorize("@ss.hasPermi('manage:inventory:list')")
    @GetMapping("/alerts")
    public TableDataInfo getAlerts()
    {
        startPage();
        List<InventoryAlertDto> alerts = inventoryService.generateInventoryAlerts();
        return getDataTable(alerts);
    }

    /**
     * 获取补货建议列表
     *
     * <p>P1-11（破坏性变更）：返回结构由 AjaxResult 改为 TableDataInfo 并接入分页。
     * 前端消费方 inventory/index.vue 的补货建议弹窗已同步改为读 rows。
     */
    @PreAuthorize("@ss.hasPermi('manage:inventory:list')")
    @GetMapping("/restock")
    public TableDataInfo getRestockSuggestions()
    {
        startPage();
        List<RestockSuggestionDto> suggestions = inventoryService.generateRestockSuggestions();
        return getDataTable(suggestions);
    }
}
