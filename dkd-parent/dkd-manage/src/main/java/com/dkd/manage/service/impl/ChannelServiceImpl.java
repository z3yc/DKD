package com.dkd.manage.service.impl;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
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

        // P1-10 SELECT 侧 N+1 整改：原实现每条 dto 调一次 getChannelInfo（N 货道 = N 次 SQL）。
        // 改为按设备分组、每台设备一次查询建内存索引，循环内 O(1) 命中——一次货道配置提交
        // 通常只涉及 1~2 台设备，查询次数从「货道数（几十）」降为「设备数」。
        // innerCode 的取值口径与单条查询保持一致：dto.innerCode 优先，回退外层 ChannelConfigDto.innerCode。
        Map<String, Channel> channelByKey = new HashMap<>();
        Set<String> innerCodes = channelConfigDto.getChannelList().stream()
                .filter(Objects::nonNull)
                .map(dto -> dto.getInnerCode() != null ? dto.getInnerCode() : channelConfigDto.getInnerCode())
                .filter(Objects::nonNull)
                .collect(Collectors.toSet());
        for (String innerCode : innerCodes) {
            for (Channel ch : channelMapper.selectChannelListByInnerCode(innerCode)) {
                channelByKey.put(innerCode + "|" + ch.getChannelCode(), ch);
            }
        }

        // 将dto转为po对象
        List<Channel> channelList = channelConfigDto.getChannelList().stream().map(dto -> {
            if (dto == null) {
                return null;
            }
            // dto.innerCode 优先，否则回退到外层 ChannelConfigDto.innerCode
            String innerCode = dto.getInnerCode() != null ? dto.getInnerCode() : channelConfigDto.getInnerCode();
            // 从预载索引命中货道（替代逐条查库）；null channelCode / null innerCode 与旧 SQL
            // 「= null 永不匹配」语义一致：索引未命中即按「货道不存在」过滤
            Channel channel = channelByKey.get(innerCode + "|" + dto.getChannelCode());
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
        // UPDATE 侧刻意逐条：规避 JDBC 多语句执行限制（allowMultiQueries），
        // 逐条按主键 UPDATE 是索引写、N = 货道数（几十），收益不足以抵消拼 CASE WHEN 批量语句的风险
        int affected = 0;
        for (Channel ch : channelList) {
            affected += channelMapper.updateChannel(ch);
        }
        // 若本次是幂等更新（例如原值相同或已为空），受影响行数可能为0，但仍应视为成功
        return affected == 0 ? 1 : affected;
    }
}
