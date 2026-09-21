package com.dkd.manage.controller;

import java.util.List;
import javax.servlet.http.HttpServletResponse;
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
import com.dkd.manage.domain.Order;
import com.dkd.manage.domain.dto.OrderDetailDto;
import com.dkd.manage.service.IOrderService;
import com.dkd.common.utils.poi.ExcelUtil;
import com.dkd.common.core.page.TableDataInfo;

/**
 * 订单管理Controller
 * 
 * @author itheima
 * @date 2024-07-29
 */
@RestController
@RequestMapping("/manage/order")
public class OrderController extends BaseController
{
    @Autowired
    private IOrderService orderService;

    /**
     * 查询订单管理列表
     */
    @PreAuthorize("@ss.hasPermi('manage:order:list')")
    @GetMapping("/list")
    public TableDataInfo list(Order order)
    {
        startPage();
        List<Order> list = orderService.selectOrderList(order);
        return getDataTable(list);
    }

    /**
     * 导出订单管理列表
     */
    @PreAuthorize("@ss.hasPermi('manage:order:export')")
    @Log(title = "订单管理", businessType = BusinessType.EXPORT)
    @PostMapping("/export")
    public void export(HttpServletResponse response, Order order)
    {
        List<Order> list = orderService.selectOrderList(order);
        ExcelUtil<Order> util = new ExcelUtil<Order>(Order.class);
        util.exportExcel(response, list, "订单管理数据");
    }

    /**
     * 获取订单管理详细信息
     */
    @PreAuthorize("@ss.hasPermi('manage:order:query')")
    @GetMapping(value = "/{id}")
    public AjaxResult getInfo(@PathVariable("id") Long id)
    {
        return success(orderService.selectOrderById(id));
    }

    /**
     * 新增订单管理
     */
    @PreAuthorize("@ss.hasPermi('manage:order:add')")
    @Log(title = "订单管理", businessType = BusinessType.INSERT)
    @PostMapping
    public AjaxResult add(@RequestBody Order order)
    {
        return toAjax(orderService.insertOrder(order));
    }

    /**
     * 修改订单管理
     */
    @PreAuthorize("@ss.hasPermi('manage:order:edit')")
    @Log(title = "订单管理", businessType = BusinessType.UPDATE)
    @PutMapping
    public AjaxResult edit(@RequestBody Order order)
    {
        return toAjax(orderService.updateOrder(order));
    }

    /**
     * 删除订单管理
     */
    @PreAuthorize("@ss.hasPermi('manage:order:remove')")
    @Log(title = "订单管理", businessType = BusinessType.DELETE)
	@DeleteMapping("/{ids}")
    public AjaxResult remove(@PathVariable Long[] ids)
    {
        return toAjax(orderService.deleteOrderByIds(ids));
    }

    /**
     * 获取订单详细信息
     */
    @PreAuthorize("@ss.hasPermi('manage:order:query')")
    @GetMapping("/detail/{id}")
    public AjaxResult getOrderDetail(@PathVariable("id") Long id)
    {
        try {
            OrderDetailDto orderDetail = orderService.getOrderDetail(id);
            return success(orderDetail);
        } catch (Exception e) {
            logger.error("获取订单详情失败，订单ID: {}, 错误信息: {}", id, e.getMessage());
            return error("获取订单详情失败: " + e.getMessage());
        }
    }

    /**
     * 取消订单
     */
    @PreAuthorize("@ss.hasPermi('manage:order:edit')")
    @Log(title = "取消订单", businessType = BusinessType.UPDATE)
    @PutMapping("/cancel/{id}")
    public AjaxResult cancelOrder(@PathVariable("id") Long id, @RequestBody String cancelDesc)
    {
        return toAjax(orderService.cancelOrder(id, cancelDesc));
    }

    /**
     * 申请退款
     */
    @PreAuthorize("@ss.hasPermi('manage:order:edit')")
    @Log(title = "申请退款", businessType = BusinessType.UPDATE)
    @PutMapping("/refund/{id}")
    public AjaxResult refundOrder(@PathVariable("id") Long id)
    {
        return toAjax(orderService.refundOrder(id));
    }
}
