package com.dkd.manage.service.impl;

import java.util.ArrayList;
import java.util.List;

import cn.hutool.core.bean.BeanUtil;
import com.dkd.common.constant.DkdContants;
import com.dkd.common.utils.DateUtils;
import com.dkd.common.utils.uuid.UUID;
import com.dkd.common.utils.uuid.UUIDUtils;
import com.dkd.manage.domain.Channel;
import com.dkd.manage.domain.Node;
import com.dkd.manage.domain.VmType;
import com.dkd.manage.service.IChannelService;
import com.dkd.manage.service.INodeService;
import com.dkd.manage.service.IVmTypeService;
import org.springframework.beans.BeanUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import com.dkd.manage.mapper.VendingMachineMapper;
import com.dkd.manage.domain.VendingMachine;
import com.dkd.manage.service.IVendingMachineService;
import org.springframework.transaction.annotation.Transactional;

/**
 * 设备管理Service业务层处理
 * 
 * @author ruoyi
 * @date 2025-11-06
 */
@Service
public class VendingMachineServiceImpl implements IVendingMachineService
{
    @Autowired
    private VendingMachineMapper vendingMachineMapper;

    @Autowired
    private IVmTypeService vmTypeService;

    @Autowired
    private INodeService nodeService;

    @Autowired
    private IChannelService channelService;

    /**
     * 查询设备管理
     * 
     * @param id 设备管理主键
     * @return 设备管理
     */
    @Override
    public VendingMachine selectVendingMachineById(Long id)
    {
        return vendingMachineMapper.selectVendingMachineById(id);
    }

    /**
     * 查询设备管理列表
     * 
     * @param vendingMachine 设备管理
     * @return 设备管理
     */
    @Override
    public List<VendingMachine> selectVendingMachineList(VendingMachine vendingMachine)
    {
        return vendingMachineMapper.selectVendingMachineList(vendingMachine);
    }

    /**
     * 新增设备管理
     * 
     * @param vendingMachine 设备管理
     * @return 结果
     */
    @Transactional
    @Override
    public int insertVendingMachine(VendingMachine vendingMachine)
    {
        //新增设备表
        //生成1-8位的唯一标识，补充货道编号
        String innerCode = UUIDUtils.getUUID();
        vendingMachine.setInnerCode(innerCode);
        //查询收货机类型
        VmType vmType = vmTypeService.selectVmTypeById(vendingMachine.getVmTypeId());
        vendingMachine.setChannelMaxCapacity(vmType.getChannelMaxCapacity());
        //点位id补充点位数据
        Node node = nodeService.selectNodeById(vendingMachine.getNodeId());
        BeanUtil.copyProperties(node, vendingMachine,"id");//商圈类型、区域、合作商
        vendingMachine.setAddr(node.getAddress());//设备地址
        //设备状态
        vendingMachine.setVmStatus(DkdContants.VM_STATUS_NODEPLOY);//0未投放
        vendingMachine.setCreateTime(DateUtils.getNowDate());//创建时间
        vendingMachine.setUpdateTime(DateUtils.getNowDate());//更新时间
        //保存
        vendingMachine.setClientId(UUID.randomUUID().toString());
        int result = vendingMachineMapper.insertVendingMachine(vendingMachine);
        //新增货道
        //声明货道集合
        List<Channel> channelList = new ArrayList<>();
        //双层for循环
        for (int i = 0; i < vmType.getVmRow(); i++) {//外层行遍历
            for (int j = 0; j < vmType.getVmCol(); j++) {//内层列遍历
                //封装货道对象
               Channel channel = new Channel();
                channel.setChannelCode(i+"-"+j);//货道编号
                channel.setVmId(vendingMachine.getId());//售货机id
                channel.setInnerCode(vendingMachine.getInnerCode());//售货机编号
                channel.setMaxCapacity(vmType.getChannelMaxCapacity());//货道容量
                channel.setCreateTime(DateUtils.getNowDate());
                channel.setUpdateTime(DateUtils.getNowDate());
                //保存货道
//                channelService.insertChannel(channel);

                channelList.add(channel);
            }

        }
        //保存货道
        channelService.batchInsertChannels(channelList);


        return result;
    }

    /**
     * 修改设备管理
     * 
     * @param vendingMachine 设备管理
     * @return 结果
     */
    @Override
    public int updateVendingMachine(VendingMachine vendingMachine)
    {
        if(vendingMachine.getNodeId()!=null){
            //查询点位表，补充区域合作商等信息
            Node node = nodeService.selectNodeById(vendingMachine.getNodeId());
            BeanUtils.copyProperties(node, vendingMachine,"id");//商圈类型、区域、合作商
            vendingMachine.setAddr(node.getAddress());//设置地址
        }
        vendingMachine.setUpdateTime(DateUtils.getNowDate());//更新时间
        return vendingMachineMapper.updateVendingMachine(vendingMachine);
    }

    /**
     * 批量删除设备管理
     * 
     * @param ids 需要删除的设备管理主键
     * @return 结果
     */
    @Override
    public int deleteVendingMachineByIds(Long[] ids)
    {
        return vendingMachineMapper.deleteVendingMachineByIds(ids);
    }

    /**
     * 删除设备管理信息
     * 
     * @param id 设备管理主键
     * @return 结果
     */
    @Override
    public int deleteVendingMachineById(Long id)
    {
        return vendingMachineMapper.deleteVendingMachineById(id);
    }
    /**
     * 根据设备编号查询设备信息
     *
     * @param innerCode 设备管理
     * @return 设备管理集合
     */
    @Override
    public VendingMachine selectVendingMachineByInnerCode(String innerCode) {
        return vendingMachineMapper.selectVendingMachineByInnerCode(innerCode);
    }
}
