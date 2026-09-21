package com.dkd.manage.service.impl;

import java.util.List;
import java.util.Objects;
import java.util.stream.Collectors;

import com.dkd.common.utils.DateUtils;
import com.dkd.manage.domain.dto.ChannelConfigDto;
import com.dkd.manage.domain.vo.ChannelVo;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import com.dkd.manage.mapper.ChannelMapper;
import com.dkd.manage.domain.Channel;
import com.dkd.manage.service.IChannelService;

/**
 * 售货机货道Service业务层处理
 *
 * @author ruoyi
 * @date 2025-11-06
 */
@Service
public class ChannelServiceImpl implements IChannelService
{
    @Autowired
    private ChannelMapper channelMapper;

    /**
     * 查询售货机货道
     *
     * @param id 售货机货道主键
     * @return 售货机货道
     */
    @Override
    public Channel selectChannelById(Long id)
    {
        return channelMapper.selectChannelById(id);
    }

    /**
     * 查询售货机货道列表
     *
     * @param channel 售货机货道
     * @return 售货机货道
     */
    @Override
    public List<Channel> selectChannelList(Channel channel)
    {
        return channelMapper.selectChannelList(channel);
    }

    /**
     * 新增售货机货道
     *
     * @param channel 售货机货道
     * @return 结果
     */
    @Override
    public int insertChannel(Channel channel)
    {
        channel.setCreateTime(DateUtils.getNowDate());
        return channelMapper.insertChannel(channel);
    }

    /**
     * 修改售货机货道
     *
     * @param channel 售货机货道
     * @return 结果
     */
    @Override
    public int updateChannel(Channel channel)
    {
        channel.setUpdateTime(DateUtils.getNowDate());
        return channelMapper.updateChannel(channel);
    }

    /**
     * 批量删除售货机货道
     *
     * @param ids 需要删除的售货机货道主键
     * @return 结果
     */
    @Override
    public int deleteChannelByIds(Long[] ids)
    {
        return channelMapper.deleteChannelByIds(ids);
    }

    /**
     * 删除售货机货道信息
     *
     * @param id 售货机货道主键
     * @return 结果
     */
    @Override
    public int deleteChannelById(Long id)
    {
        return channelMapper.deleteChannelById(id);
    }

    /**
     * 批量新增
     * @param channels
     * @return
     */
    @Override
    public int batchInsertChannels(List<Channel> channels) {
        return channelMapper.batchInsertChannels(channels);
    }

    /**
     * 根据商品id集合统计货道数量
     * @param skuIds
     * * @return 统计结果
     * **/

    @Override
    public int countChannelBySkuIds(Long[] skuIds) {
        return channelMapper.countChannelBySuIds(skuIds);
    }

    /**
     * 根据货道id集合查询货道信息
     *
     * @param innerCode
     * @return ChannelVo
     */

    @Override
    public List<ChannelVo> selectChannelVoListByInnerCode(String innerCode) {
        return channelMapper.selectChannelVoListByInnerCode(innerCode);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public int setChannel(ChannelConfigDto channelConfigDto) {
        // 入参判空保护
        if (channelConfigDto == null || channelConfigDto.getChannelList() == null || channelConfigDto.getChannelList().isEmpty()) {
            return 0;
        }

        // 将dto转为po对象
        List<Channel> channelList = channelConfigDto.getChannelList().stream().map(dto -> {
            if (dto == null) {
                return null;
            }
            // dto.innerCode 优先，否则回退到外层 ChannelConfigDto.innerCode
            String innerCode = dto.getInnerCode() != null ? dto.getInnerCode() : channelConfigDto.getInnerCode();
            //根据售货机编号和货道编号查询货道信息
            Channel channel = channelMapper.getChannelInfo(innerCode, dto.getChannelCode());
            if (channel != null) {
                //关联最新商品
                channel.setSkuId(dto.getSkuId());
                //修改时间
                channel.setUpdateTime(DateUtils.getNowDate());
            }
            return channel;
        })
        // 过滤掉未查询到的空货道，避免 MyBatis 在 foreach 中处理 null 导致 OGNL 异常
        .filter(Objects::nonNull)
        .collect(Collectors.toList());
        // 若没有需要更新的记录，直接返回，避免执行空 SQL 导致 "SQL string cannot be empty"
        if (channelList.isEmpty()) {
            return 0;
        }
        // 避免 JDBC 多语句执行限制，逐条更新，计数汇总
        int affected = 0;
        for (Channel ch : channelList) {
            affected += channelMapper.updateChannel(ch);
        }
        // 若本次是幂等更新（例如原值相同或已为空），受影响行数可能为0，但仍应视为成功
        return affected == 0 ? 1 : affected;



    }
}
