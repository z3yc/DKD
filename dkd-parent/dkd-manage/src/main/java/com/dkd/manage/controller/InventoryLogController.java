package com.dkd.manage.controller;

import java.util.List;
import javax.servlet.http.HttpServletResponse;

import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import com.dkd.common.annotation.Log;
import com.dkd.common.core.controller.BaseController;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.common.enums.BusinessType;
import com.dkd.manage.domain.InventoryLog;
import com.dkd.manage.service.IInventoryLogService;
import com.dkd.common.utils.poi.ExcelUtil;
import com.dkd.common.core.page.TableDataInfo;

/**
 * 库存变动日志Controller
 *
 * @author ruoyi
 * @date 2025-01-08
 */
@RestController
@RequestMapping("/manage/inventoryLog")
public class InventoryLogController extends BaseController
{
    @Autowired
    private IInventoryLogService inventoryLogService;

    /**
     * 查询库存变动日志列表
     */
    @PreAuthorize("@ss.hasPermi('manage:inventoryLog:list')")
    @GetMapping("/list")
    public TableDataInfo list(InventoryLog inventoryLog)
    {
        startPage();
        List<InventoryLog> list = inventoryLogService.selectInventoryLogList(inventoryLog);
        return getDataTable(list);
    }

    /**
     * 导出库存变动日志列表
     */
    @PreAuthorize("@ss.hasPermi('manage:inventoryLog:export')")
    @Log(title = "库存变动日志", businessType = BusinessType.EXPORT)
    @PostMapping("/export")
    public void export(HttpServletResponse response, InventoryLog inventoryLog)
    {
        List<InventoryLog> list = inventoryLogService.selectInventoryLogList(inventoryLog);
        ExcelUtil<InventoryLog> util = new ExcelUtil<InventoryLog>(InventoryLog.class);
        util.exportExcel(response, list, "库存变动日志数据");
    }

    /**
     * 获取库存变动日志详细信息
     */
    @PreAuthorize("@ss.hasPermi('manage:inventoryLog:query')")
    @GetMapping(value = "/{id}")
    public AjaxResult getInfo(@PathVariable("id") Long id)
    {
        return success(inventoryLogService.selectInventoryLogById(id));
    }

    /**
     * 新增库存变动日志
     */
    @PreAuthorize("@ss.hasPermi('manage:inventoryLog:add')")
    @Log(title = "库存变动日志", businessType = BusinessType.INSERT)
    @PostMapping
    public AjaxResult add(@RequestBody InventoryLog inventoryLog)
    {
        return toAjax(inventoryLogService.insertInventoryLog(inventoryLog));
    }

    /**
     * 删除库存变动日志
     */
    @PreAuthorize("@ss.hasPermi('manage:inventoryLog:remove')")
    @Log(title = "库存变动日志", businessType = BusinessType.DELETE)
    @DeleteMapping("/{ids}")
    public AjaxResult remove(@PathVariable Long[] ids)
    {
        return toAjax(inventoryLogService.deleteInventoryLogByIds(ids));
    }

    /**
     * 根据售货机ID查询库存变动日志
     */
    @PreAuthorize("@ss.hasPermi('manage:inventoryLog:list')")
    @GetMapping("/vm/{vmId}")
    public TableDataInfo listByVmId(@PathVariable("vmId") Long vmId)
    {
        startPage();
        List<InventoryLog> list = inventoryLogService.selectByVmId(vmId);
        return getDataTable(list);
    }

    /**
     * 根据商品ID查询库存变动日志
     */
    @PreAuthorize("@ss.hasPermi('manage:inventoryLog:list')")
    @GetMapping("/sku/{skuId}")
    public TableDataInfo listBySkuId(@PathVariable("skuId") Long skuId)
    {
        startPage();
        List<InventoryLog> list = inventoryLogService.selectBySkuId(skuId);
        return getDataTable(list);
    }

    /**
     * 根据货道ID查询库存变动日志
     */
    @PreAuthorize("@ss.hasPermi('manage:inventoryLog:list')")
    @GetMapping("/channel/{channelId}")
    public TableDataInfo listByChannelId(@PathVariable("channelId") Long channelId)
    {
        startPage();
        List<InventoryLog> list = inventoryLogService.selectByChannelId(channelId);
        return getDataTable(list);
    }
}
