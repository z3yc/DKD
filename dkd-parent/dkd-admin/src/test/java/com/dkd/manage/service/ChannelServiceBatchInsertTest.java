package com.dkd.manage.service;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;

import com.dkd.manage.domain.Channel;
import com.dkd.manage.mapper.ChannelMapper;
import com.dkd.manage.service.impl.ChannelServiceImpl;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.same;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 货道批量插入分片的回归测试（P2-29）。
 *
 * <p>## 这批用例防什么
 * {@code ChannelMapper.xml} 的 {@code batchInsertChannels} 是单条 {@code INSERT ... VALUES <foreach>}。
 * 原实现把机型全部货道（vmRow×vmCol，大机型上万行）塞进一条 SQL，可能超过 MySQL
 * {@code max_allowed_packet}，表现为「建机机失败/连接被重置」。改造后按 500 行分片，
 * 本测试钉住：<b>分片边界（500/500/200）、顺序不变、影响行数累加</b>，
 * 避免以后有人把循环改回「一条 INSERT」或改错子列表区间（首片重复/尾片丢失）。
 *
 * <p>## 空列表「行为与现状一致」怎么验
 * 现状是「原样委托 mapper，由 MyBatis/数据库报错」，不是 Service 层提前返回 0。
 * 本次改造刻意不新增早返回，所以这里断言的是<b>仍然发生过一次、且参数是同一个空列表对象</b>
 * 的 mapper 调用——用 mock 只能验到这一层；真库下空 VALUES 触发 SQL 语法错误的行为未变
 * （留待真库验收，不在本单测范围内）。
 *
 * <p>## 为什么用 Mockito 不连真库
 * AGENTS §8：Java 单测不依赖 MySQL/Redis；分片是纯控制流，与 SQL 执行无关。
 *
 * @author ruoyi
 * @date 2026-10-29
 */
@ExtendWith(MockitoExtension.class)
// 不同用例覆盖不同分支，未打桩的协作对象在部分分支不会被调用
@MockitoSettings(strictness = Strictness.LENIENT)
class ChannelServiceBatchInsertTest
{
    @Mock
    private ChannelMapper channelMapper;

    @InjectMocks
    private ChannelServiceImpl channelService;

    /**
     * 构造 n 条货道，channelCode 带序号便于断言顺序与区间。
     *
     * @param n 条数
     * @return 货道列表
     */
    private List<Channel> channels(int n)
    {
        List<Channel> list = new ArrayList<Channel>(n);
        for (int i = 0; i < n; i++) {
            Channel channel = new Channel();
            channel.setChannelCode("ch-" + i);
            channel.setInnerCode("A1000001");
            list.add(channel);
        }
        return list;
    }

    @Test
    void 一千二百行按五百行分三片插入且返回合计()
    {
        List<Channel> list = channels(1200);
        // 三片分别影响 500/500/200 行，合计 1200（证明累加而不是只返回最后一片）
        when(channelMapper.batchInsertChannels(anyList())).thenReturn(500, 500, 200);

        int affected = channelService.batchInsertChannels(list);

        assertThat(affected).as("返回值必须是各分片影响行数之和").isEqualTo(1200);
        ArgumentCaptor<List> captor = ArgumentCaptor.forClass(List.class);
        verify(channelMapper, times(3)).batchInsertChannels(captor.capture());
        List<List> calls = captor.getAllValues();
        assertThat(calls).hasSize(3);
        // 分片边界：500 / 500 / 200
        assertThat(calls.get(0)).hasSize(500);
        assertThat(calls.get(1)).hasSize(500);
        assertThat(calls.get(2)).hasSize(200);
        // 顺序与连续性：第 0 条在最前、第 499/500/1199 条落在正确位置，不重不漏
        assertThat(calls.get(0).get(0)).isSameAs(list.get(0));
        assertThat(calls.get(0).get(499)).isSameAs(list.get(499));
        assertThat(calls.get(1).get(0)).isSameAs(list.get(500));
        assertThat(calls.get(2).get(199)).isSameAs(list.get(1199));
    }

    @Test
    void 超过分片大小一行也要分两片()
    {
        List<Channel> list = channels(501);
        when(channelMapper.batchInsertChannels(anyList())).thenReturn(500, 1);

        assertThat(channelService.batchInsertChannels(list)).isEqualTo(501);

        ArgumentCaptor<List> captor = ArgumentCaptor.forClass(List.class);
        verify(channelMapper, times(2)).batchInsertChannels(captor.capture());
        assertThat(captor.getAllValues().get(0)).hasSize(500);
        assertThat(captor.getAllValues().get(1)).hasSize(1);
        assertThat(captor.getAllValues().get(1).get(0)).isSameAs(list.get(500));
    }

    @Test
    void 小列表只调用一次且原对象透传()
    {
        List<Channel> list = channels(3);
        when(channelMapper.batchInsertChannels(same(list))).thenReturn(3);

        assertThat(channelService.batchInsertChannels(list)).isEqualTo(3);

        // 小列表不切片：一次调用、参数就是调用方传进来的同一个 List 对象
        verify(channelMapper, times(1)).batchInsertChannels(same(list));
    }

    @Test
    void 空列表行为与改造前一致仍原样委托mapper()
    {
        List<Channel> empty = Collections.emptyList();
        when(channelMapper.batchInsertChannels(same(empty))).thenReturn(0);

        assertThat(channelService.batchInsertChannels(empty)).isEqualTo(0);

        // 防「顺手加了 if (isEmpty()) return 0」把行为改掉：改造前空列表也是直接委托 mapper
        verify(channelMapper, times(1)).batchInsertChannels(same(empty));
    }

    @Test
    void null列表行为与改造前一致仍原样委托mapper()
    {
        assertThat(channelService.batchInsertChannels(null)).isEqualTo(0);

        verify(channelMapper, times(1)).batchInsertChannels(null);
    }
}
