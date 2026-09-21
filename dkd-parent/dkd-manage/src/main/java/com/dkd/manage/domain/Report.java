package com.dkd.manage.domain;

import java.util.Date;
import com.fasterxml.jackson.annotation.JsonFormat;
import org.apache.commons.lang3.builder.ToStringBuilder;
import org.apache.commons.lang3.builder.ToStringStyle;
import com.dkd.common.annotation.Excel;
import com.dkd.common.core.domain.BaseEntity;

/**
 * AI报表对象 tb_report
 *
 * @author ruoyi
 * @date 2026-04-22
 */
public class Report extends BaseEntity
{
    private static final long serialVersionUID = 1L;

    private Long id;

    @Excel(name = "报表类型")
    private String reportType;

    @JsonFormat(pattern = "yyyy-MM-dd")
    @Excel(name = "报表日期", width = 30, dateFormat = "yyyy-MM-dd")
    private Date reportDate;

    @Excel(name = "总收入(分)")
    private Long totalRevenue;

    @Excel(name = "成功订单数")
    private Integer totalOrders;

    @Excel(name = "设备总数")
    private Integer totalDevices;

    @Excel(name = "在线设备数")
    private Integer onlineDevices;

    @Excel(name = "故障设备数")
    private Integer faultDevices;

    @Excel(name = "库存不足设备数")
    private Integer lowStockDevices;

    private String topProducts;

    private String topNodes;

    private String anomalyAlerts;

    private String aiAnalysis;

    private Integer retryCount;

    private Integer status;

    public void setId(Long id)
    {
        this.id = id;
    }

    public Long getId()
    {
        return id;
    }

    public void setReportType(String reportType)
    {
        this.reportType = reportType;
    }

    public String getReportType()
    {
        return reportType;
    }

    public void setReportDate(Date reportDate)
    {
        this.reportDate = reportDate;
    }

    public Date getReportDate()
    {
        return reportDate;
    }

    public void setTotalRevenue(Long totalRevenue)
    {
        this.totalRevenue = totalRevenue;
    }

    public Long getTotalRevenue()
    {
        return totalRevenue;
    }

    public void setTotalOrders(Integer totalOrders)
    {
        this.totalOrders = totalOrders;
    }

    public Integer getTotalOrders()
    {
        return totalOrders;
    }

    public void setTotalDevices(Integer totalDevices)
    {
        this.totalDevices = totalDevices;
    }

    public Integer getTotalDevices()
    {
        return totalDevices;
    }

    public void setOnlineDevices(Integer onlineDevices)
    {
        this.onlineDevices = onlineDevices;
    }

    public Integer getOnlineDevices()
    {
        return onlineDevices;
    }

    public void setFaultDevices(Integer faultDevices)
    {
        this.faultDevices = faultDevices;
    }

    public Integer getFaultDevices()
    {
        return faultDevices;
    }

    public void setLowStockDevices(Integer lowStockDevices)
    {
        this.lowStockDevices = lowStockDevices;
    }

    public Integer getLowStockDevices()
    {
        return lowStockDevices;
    }

    public void setTopProducts(String topProducts)
    {
        this.topProducts = topProducts;
    }

    public String getTopProducts()
    {
        return topProducts;
    }

    public void setTopNodes(String topNodes)
    {
        this.topNodes = topNodes;
    }

    public String getTopNodes()
    {
        return topNodes;
    }

    public void setAnomalyAlerts(String anomalyAlerts)
    {
        this.anomalyAlerts = anomalyAlerts;
    }

    public String getAnomalyAlerts()
    {
        return anomalyAlerts;
    }

    public void setAiAnalysis(String aiAnalysis)
    {
        this.aiAnalysis = aiAnalysis;
    }

    public String getAiAnalysis()
    {
        return aiAnalysis;
    }

    public void setRetryCount(Integer retryCount)
    {
        this.retryCount = retryCount;
    }

    public Integer getRetryCount()
    {
        return retryCount;
    }

    public void setStatus(Integer status)
    {
        this.status = status;
    }

    public Integer getStatus()
    {
        return status;
    }

    @Override
    public String toString() {
        return new ToStringBuilder(this,ToStringStyle.MULTI_LINE_STYLE)
            .append("id", getId())
            .append("reportType", getReportType())
            .append("reportDate", getReportDate())
            .append("totalRevenue", getTotalRevenue())
            .append("totalOrders", getTotalOrders())
            .append("totalDevices", getTotalDevices())
            .append("onlineDevices", getOnlineDevices())
            .append("faultDevices", getFaultDevices())
            .append("lowStockDevices", getLowStockDevices())
            .append("topProducts", getTopProducts())
            .append("topNodes", getTopNodes())
            .append("anomalyAlerts", getAnomalyAlerts())
            .append("aiAnalysis", getAiAnalysis())
            .append("retryCount", getRetryCount())
            .append("status", getStatus())
            .append("createTime", getCreateTime())
            .append("updateTime", getUpdateTime())
            .toString();
    }
}
