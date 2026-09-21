package com.dkd.manage.controller;

import java.util.List;
import javax.servlet.http.HttpServletResponse;

import com.dkd.manage.domain.dto.InventoryAlertDto;
import com.dkd.manage.domain.dto.RestockSuggestionDto;
import com.dkd.manage.domain.Channel;
import com.dkd.manage.domain.Sku;
import com.dkd.manage.domain.VendingMachine;
import com.dkd.manage.mapper.ChannelMapper;
import com.dkd.manage.mapper.SkuMapper;
import com.dkd.manage.mapper.VendingMachineMapper;
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

    @Autowired
    private VendingMachineMapper vendingMachineMapper;

    @Autowired
    private SkuMapper skuMapper;

    @Autowired
    private ChannelMapper channelMapper;

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
     */
    @PreAuthorize("@ss.hasPermi('manage:inventory:add')")
    @Log(title = "库存", businessType = BusinessType.INSERT)
    @PostMapping
    public AjaxResult add(@RequestBody Inventory inventory)
    {
        // 如果前端传递的是显示字段，则根据显示字段查找对应的ID
        if (inventory.getVmId() == null && inventory.getInnerCode() != null && !inventory.getInnerCode().isEmpty())
        {
            VendingMachine vm = vendingMachineMapper.selectVendingMachineByInnerCode(inventory.getInnerCode());
            if (vm == null)
            {
                return AjaxResult.error("售货机编号【" + inventory.getInnerCode() + "】不存在");
            }
            inventory.setVmId(vm.getId());
        }

        if (inventory.getSkuId() == null && inventory.getSkuName() != null && !inventory.getSkuName().isEmpty())
        {
            Sku sku = skuMapper.selectSkuBySkuName(inventory.getSkuName());
            if (sku == null)
            {
                return AjaxResult.error("商品名称【" + inventory.getSkuName() + "】不存在");
            }
            inventory.setSkuId(sku.getSkuId());
        }

        if (inventory.getChannelId() == null && inventory.getInnerCode() != null && !inventory.getInnerCode().isEmpty()
            && inventory.getChannelCode() != null && !inventory.getChannelCode().isEmpty())
        {
            Channel channel = channelMapper.getChannelInfo(inventory.getInnerCode(), inventory.getChannelCode());
            if (channel == null)
            {
                return AjaxResult.error("售货机【" + inventory.getInnerCode() + "】的货道【" + inventory.getChannelCode() + "】不存在");
            }
            inventory.setChannelId(channel.getId());
        }

        // 参数校验
        if (inventory.getVmId() == null)
        {
            return AjaxResult.error("售货机ID不能为空");
        }
        if (inventory.getSkuId() == null)
        {
            return AjaxResult.error("商品ID不能为空");
        }
        if (inventory.getChannelId() == null)
        {
            return AjaxResult.error("货道ID不能为空");
        }
        if (inventory.getQuantity() == null)
        {
            return AjaxResult.error("库存数量不能为空");
        }

        return toAjax(inventoryService.insertInventory(inventory));
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
     */
    @PreAuthorize("@ss.hasPermi('manage:inventory:list')")
    @GetMapping("/alerts")
    public AjaxResult getAlerts()
    {
        List<InventoryAlertDto> alerts = inventoryService.generateInventoryAlerts();
        return success(alerts);
    }

    /**
     * 获取补货建议列表
     */
    @PreAuthorize("@ss.hasPermi('manage:inventory:list')")
    @GetMapping("/restock")
    public AjaxResult getRestockSuggestions()
    {
        List<RestockSuggestionDto> suggestions = inventoryService.generateRestockSuggestions();
        return success(suggestions);
    }
}
