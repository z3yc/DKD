package com.dkd.manage.domain.dto;

import com.fasterxml.jackson.annotation.JsonFormat;

import java.util.Date;

/**
 * 订单详情DTO
 * 
 * @author itheima
 * @date 2024-07-29
 */
public class OrderDetailDto {
    
    /** 订单ID */
    private Long id;
    
    /** 订单编号 */
    private String orderNo;
    
    /** 商品名称 */
    private String skuName;
    
    /** 订单金额 */
    private Long amount;
    
    /** 订单状态:0-待支付;1-支付完成;2-出货成功;3-出货失败;4-已取消 */
    private Long status;
    
    /** 订单状态描述 */
    private String statusDesc;
    
    /** 设备编号 */
    private String innerCode;
    
    /** 设备地址 */
    private String addr;
    
    /** 创建时间 */
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private Date createTime;
    
    /** 完成时间 */
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private Date updateTime;
    
    /** 支付方式：1支付宝 2微信 */
    private String payType;
    
    /** 支付方式描述 */
    private String payTypeDesc;
    
    /** 交易订单号 */
    private String thirdNo;
    
    /** 是否可以退款 */
    private Boolean canRefund;
    
    /** 是否可以取消 */
    private Boolean canCancel;

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getOrderNo() {
        return orderNo;
    }

    public void setOrderNo(String orderNo) {
        this.orderNo = orderNo;
    }

    public String getSkuName() {
        return skuName;
    }

    public void setSkuName(String skuName) {
        this.skuName = skuName;
    }

    public Long getAmount() {
        return amount;
    }

    public void setAmount(Long amount) {
        this.amount = amount;
    }

    public Long getStatus() {
        return status;
    }

    public void setStatus(Long status) {
        this.status = status;
    }

    public String getStatusDesc() {
        return statusDesc;
    }

    public void setStatusDesc(String statusDesc) {
        this.statusDesc = statusDesc;
    }

    public String getInnerCode() {
        return innerCode;
    }

    public void setInnerCode(String innerCode) {
        this.innerCode = innerCode;
    }

    public String getAddr() {
        return addr;
    }

    public void setAddr(String addr) {
        this.addr = addr;
    }

    public Date getCreateTime() {
        return createTime;
    }

    public void setCreateTime(Date createTime) {
        this.createTime = createTime;
    }

    public Date getUpdateTime() {
        return updateTime;
    }

    public void setUpdateTime(Date updateTime) {
        this.updateTime = updateTime;
    }

    public String getPayType() {
        return payType;
    }

    public void setPayType(String payType) {
        this.payType = payType;
    }

    public String getPayTypeDesc() {
        return payTypeDesc;
    }

    public void setPayTypeDesc(String payTypeDesc) {
        this.payTypeDesc = payTypeDesc;
    }

    public String getThirdNo() {
        return thirdNo;
    }

    public void setThirdNo(String thirdNo) {
        this.thirdNo = thirdNo;
    }

    public Boolean getCanRefund() {
        return canRefund;
    }

    public void setCanRefund(Boolean canRefund) {
        this.canRefund = canRefund;
    }

    public Boolean getCanCancel() {
        return canCancel;
    }

    public void setCanCancel(Boolean canCancel) {
        this.canCancel = canCancel;
    }
}
