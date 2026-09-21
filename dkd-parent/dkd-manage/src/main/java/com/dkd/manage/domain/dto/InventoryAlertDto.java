package com.dkd.manage.domain.dto;

import com.dkd.manage.domain.Inventory;

/**
 * 库存预警DTO
 *
 * @author ruoyi
 * @date 2025-01-08
 */
public class InventoryAlertDto
{
    /** 预警类型：1-低库存预警 2-缺货预警 */
    private Integer alertType;

    /** 预警级别：1-普通 2-紧急 3-严重 */
    private Integer alertLevel;

    /** 库存信息 */
    private Inventory inventory;

    /** 预警消息 */
    private String alertMessage;

    /** 建议 */
    private String suggestion;

    public Integer getAlertType()
    {
        return alertType;
    }

    public void setAlertType(Integer alertType)
    {
        this.alertType = alertType;
    }

    public Integer getAlertLevel()
    {
        return alertLevel;
    }

    public void setAlertLevel(Integer alertLevel)
    {
        this.alertLevel = alertLevel;
    }

    public Inventory getInventory()
    {
        return inventory;
    }

    public void setInventory(Inventory inventory)
    {
        this.inventory = inventory;
    }

    public String getAlertMessage()
    {
        return alertMessage;
    }

    public void setAlertMessage(String alertMessage)
    {
        this.alertMessage = alertMessage;
    }

    public String getSuggestion()
    {
        return suggestion;
    }

    public void setSuggestion(String suggestion)
    {
        this.suggestion = suggestion;
    }
}
