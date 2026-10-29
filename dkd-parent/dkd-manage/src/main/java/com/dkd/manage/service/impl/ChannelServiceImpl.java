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
    /**
     * 批量插入货道的分片大小（P2-29）。
     *
     * <p>为什么是 500：{@code ChannelMapper.xml} 的 {@code batchInsertChannels} 是单条
     * {@code INSERT ... VALUES <foreach>}，行数越多单条 SQL 的包体越大；机型 vmRow×vmCol
     * 可达上万行，单条语句可能超过 MySQL {@code max_allowed_packet}（默认 4MB/64MB），
     * 表现为插入失败或连接被重置。每行 8 列、约百字节量级，500 行 ≈ 几十 KB，
     * 离默认包上限有两个数量级余量，同时把 network/参数绑定的往返次数压在个位数。
     */
    private static final int CHANNEL_INSERT_BATCH_SIZE = 500;

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
     * 批量新增货道（按 {@link #CHANNEL_INSERT_BATCH_SIZE} 分片插入，P2-29）。
     *
     * <p>为什么分片：原实现把全部货道塞进一条多值 INSERT，大机型（vmRow×vmCol 上万）
     * 可能超 {@code max_allowed_packet}；分片后单条语句包体可控。分片保持原始顺序
     * （subList 只读视图，不复制元素），返回值为各片影响行数之和。
     *
     * <p>事务：本方法不加事务注解（保持既有事务语义）——调用方
     * {@code VendingMachineServiceImpl.insertVendingMachine} 在 {@code @Transactional} 内，
     * 任一分片失败整体回滚；若将来出现无事务调用方，需由调用方保证「全成或全不成」。
     *
     * <p>空列表/null/小列表（≤ 分片大小）行为保持现状：仍以原对象一次委托 mapper
     * （不新增早返回/过滤），由 MyBatis/数据库按原路径处理，不在本次分批改造中改变入参语义。
     *
     * @param channels 货道列表
     * @return 各分片影响行数合计
     */
    @Override
    public int batchInsertChannels(List<Channel> channels) {
        // 小列表（含空列表/null）保持一次调用、原对象透传：大多数机型的 vmRow×vmCol ≤ 500，
        // 不引入 subList 开销；空列表也从这里原路委托 mapper（行为与改造前一致）。
        if (channels == null || channels.size() <= CHANNEL_INSERT_BATCH_SIZE) {
            return channelMapper.batchInsertChannels(channels);
        }
        int affected = 0;
        for (int from = 0; from < channels.size(); from += CHANNEL_INSERT_BATCH_SIZE) {
            int to = Math.min(from + CHANNEL_INSERT_BATCH_SIZE, channels.size());
            affected += channelMapper.batchInsertChannels(channels.subList(from, to));
        }
        return affected;
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
