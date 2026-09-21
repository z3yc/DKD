package com.dkd.manage.domain.dto;

import lombok.Data;

@Data
public class ChannelSkuDto {
    //售货机编号
    private String innerCode;
    //商品id
    private Long skuId;
    //货道id
    private String channelCode;
}
