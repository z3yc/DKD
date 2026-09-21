package com.dkd.manage.mapper;

import java.util.List;
import com.dkd.manage.domain.Report;
import org.apache.ibatis.annotations.Param;

/**
 * 报表Mapper接口
 *
 * @author ruoyi
 * @date 2026-04-22
 */
public interface ReportMapper
{
    public Report selectReportById(Long id);

    public List<Report> selectReportList(Report report);

    public Report selectLatestReport(@Param("reportType") String reportType);

    public int insertReport(Report report);

    public int updateReport(Report report);

    public int insertOrIgnoreReport(Report report);

    public Long sumRevenueByStatusAndDateRange(@Param("status") Integer status,
                                                @Param("beginTime") String beginTime,
                                                @Param("endTime") String endTime);

    public Integer countOrdersByStatusAndDateRange(@Param("status") Integer status,
                                                    @Param("beginTime") String beginTime,
                                                    @Param("endTime") String endTime);

    public List<java.util.Map<String, Object>> topNodesByRevenue(@Param("status") Integer status,
                                                                   @Param("beginTime") String beginTime,
                                                                   @Param("endTime") String endTime);

    public List<java.util.Map<String, Object>> topProductsBySales(@Param("status") Integer status,
                                                                    @Param("beginTime") String beginTime,
                                                                    @Param("endTime") String endTime);

    public Integer countLowStockDevices();

    int deleteReportByIds(Long[] ids);
}
