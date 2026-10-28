package com.dkd.manage.mapper;

import java.util.List;
import java.util.Map;
import com.dkd.manage.domain.VendingMachine;
import org.apache.ibatis.annotations.Select;

/**
 * 设备管理Mapper接口
 * 
 * @author ruoyi
 * @date 2025-11-06
 */
public interface VendingMachineMapper 
{
    /**
     * 查询设备管理
     * 
     * @param id 设备管理主键
     * @return 设备管理
     */
    public VendingMachine selectVendingMachineById(Long id);

    /**
     * 查询设备管理列表
     * 
     * @param vendingMachine 设备管理
     * @return 设备管理集合
     */
    public List<VendingMachine> selectVendingMachineList(VendingMachine vendingMachine);

    /**
     * 新增设备管理
     * 
     * @param vendingMachine 设备管理
     * @return 结果
     */
    public int insertVendingMachine(VendingMachine vendingMachine);

    /**
     * 修改设备管理
     * 
     * @param vendingMachine 设备管理
     * @return 结果
     */
    public int updateVendingMachine(VendingMachine vendingMachine);

    /**
     * 删除设备管理
     * 
     * @param id 设备管理主键
     * @return 结果
     */
    public int deleteVendingMachineById(Long id);

    /**
     * 批量删除设备管理
     * 
     * @param ids 需要删除的数据主键集合
     * @return 结果
     */
    public int deleteVendingMachineByIds(Long[] ids);

    @Select("select * from tb_vending_machine where inner_code=#{innerCode}")
    public VendingMachine selectVendingMachineByInnerCode(String innerCode);

    /**
     * 按设备状态分组统计台数（P1-8 报表口径下推）。
     *
     * <p>为什么下推 SQL：原实现把全量设备实体拉进内存只为 {@code size()} 与逐台数状态
     * （ReportServiceImpl#buildReport），设备量上千后每次日报/周报多一次全表物化。
     *
     * <p>口径约束（与报表语义强绑定，勿改）：
     * <ul>
     *   <li>totalDevices = 各状态台数之和（含未投放/已撤机——设备总数是生命周期总量）；</li>
     *   <li>faultDevices 仅取 vm_status=2（停机）。**不能**用 {@code vm_status != 1}：
     *       未投放(0)/已撤机(3) 是正常生命周期，算成故障会让故障率虚高、AI 分析失真（业务规范 §4.5）。</li>
     * </ul>
     *
     * @return 每状态一行：{vm_status, cnt}
     */
    public List<Map<String, Object>> countGroupByVmStatus();

    /**
     * 在线设备数（P1-8 下推）：心跳 JSON {@code $.status === true}，口径与设备列表页一致。
     *
     * <p>坏 JSON / 缺心跳按离线统计：{@code JSON_VALID} 不通过即不计数，
     * 与 Java 版「解析失败记 debug 按离线」等价（坏心跳只影响该设备一台，
     * 不能让整张日报失败）。如需观测坏数据量，取 total - 统计数差值即可。
     *
     * @return 在线台数
     */
    public Integer countOnlineByRunningStatus();
}
