"""
Name : test_singup.py
Author  : 粟飞
Contact : 邮箱地址
Time    : 2025-04-03 16:41:37.114558
Desc: 	供应商在线获取电子标书
"""
import hashlib
import os, sys, re
import secrets
import allure
import pytest
from gmsofttest.gm_yaml_process_utils import read_yaml
from gmsofttest.gm_assert_utils import *
from gmsofttest.gm_parse_response_utils import *
from gmsofttest.gm_excel_utils import ReadExcel
from gmsofttest.gm_randomdata_utils import *

# 请求header
headers = {'Content-Type': 'application/json'}
form_headers = {"User-Agent": "Mozilla/5.0"}


# 读取uri等配置
uriconfig = read_yaml('conf', 'env_config', 'test_url.yaml')
api_uri = uriconfig.get('test_ebiddingonlinesignupbid_signupbidbooklist_get')  # 电子标书列表
user_uri = uriconfig.get('user_info_api')  # 用户信息
business_agree_api = uriconfig.get('business_agree_api')  # 投标人须知
sign_count_api = uriconfig.get("sign_count_api")  # 供应商投标计数
save_signup_api = uriconfig.get("test_ebiddingonlinesignupbid_save_post")  # 提交获取电子标书请求
online_signup_info_api = uriconfig.get("test_ebiddingonlinesignupbid_onlinesignupinfos_provider_get")  # 在线报名投标管理/查询在线报名列表-包含投标信息
slice_check_api = uriconfig.get("slice_check")  # 上传前询问
slice_upload_api = uriconfig.get("slice_upload")  # 上传切片
merge_succeed_api = uriconfig.get("merge_succeed")  # 询问上传结果
bid_files_api = uriconfig.get("bid_files")  # 上传文件
save_bid_api = uriconfig.get("test_ebiddingonlinesignupbid_savebidinfo_post")  # 提交投标文件请求
org_details_api = uriconfig.get("org_details")  # 企业详情

# 项目设置
# 获取当前脚本所在的目录
script_dir = os.path.dirname(os.path.abspath(__file__))

# reader = ReadExcel(r'data', f'{my_host}_{my_env}_accounts.xlsx')
# 从环境变量获取配置，没有则使用默认值
my_host = os.getenv('TEST_HOST', 'zcj')  # 默认值 zcj
my_env = os.getenv('TEST_ENV', 'show')   # 默认值 show
reader = ReadExcel('data', 'zcj_show_accounts.xlsx')
supplier_accounts = reader.read_excel_data('Sheet1')
project_conf = read_yaml('conf', 'project.yaml')

# 项目号
anoteno = project_conf.get('anoteno')

bid_file_name = project_conf.get('bid_file_name')
bid_file_list = [file.strip() for file in bid_file_name.split(',')] if bid_file_name else []
bid_file_paths = [os.path.join(script_dir, file) for file in bid_file_list]

# ===  测试准备（注：支撑单项目多包，所有供应商投标所有包) === #
# 修改Project.yaml中的项目号、投标文件（按包分别填写文件）
# 将实际投标文件放到本文件同级目录
# 读取data目录下账号，一次不要太多，避免服务器卡顿
# pytest --env={env} --host={host} test_signup.py

@pytest.fixture
def test_data_cache(account_session, set_host, log):
    """获取并缓存用户信息的fixture"""
    session, username, company = account_session
    cache = {'session': session, 'username': username, 'company': company}

    # 打印调试信息
    # log.warning(f"\nGetting user info for - username: {username}, company: {company}")

    # 1. 请求用户信息
    params = {'pageNo': 1, 'pageSize': 10}
    user_resp = session.get(f"{set_host[set_host['host']]}{user_uri}", params=params, headers=headers, verify=False)
    cache['user_id'] = user_resp.json()['userId']

    # # 打印请求详情
    # log.info(f"用户信息请求URL: {resp.url}")
    # log.info(f"用户信息User接口响应: {resp.text}")

    # 2. 获取项目及标包数据
    project_resp = session.get(
        f"{set_host[set_host['host']]}{api_uri}",
        params={'anoteno': anoteno},
        headers=headers,
        verify=False
    )
    project_data = project_resp.json()[0]
    cache.update({
        'bidbook_id': project_data['id'],
        'project_id': project_data['projectId'],
        'project_name': project_data.get('projectName', ''),
        'content': project_data['content'],
        'item_create_id': project_data['itemCreateId'],
        'packages': [
            {
                'pack_id': detail['id'],
                'pack_no': detail['packNo'],
                'if_signed': detail.get('ifAlrSignup', False)
            }
            for detail in project_data.get('details', [])
        ]
    })

    # 3.获取投标须知ID
    agree_url = f"{set_host[set_host['host']]}{business_agree_api}"
    agree_resp = session.get(agree_url, params={'agreeCodes': "EBIDDING_BID_PROTOCOL"}, headers=headers, verify=False)
    cache['agree_id'] = gm_extract_json(agree_resp.json(), '$..id')[-1]

    # 返回包含所有需要共享的数据
    return cache


@allure.epic('电子招投标服务')
@allure.feature('在线报名投标管理')
@pytest.mark.parametrize(
    'account_session',  # 仅参数化account_session
    supplier_accounts,  # 读取xls文件内容
    indirect=True,  # 使用间接fixture
    ids=lambda x: f"账号_{x[0]}_单位_{x[1]}" if x[1] else f"账号_{x[0]}"  # 为每个账号生成易读的测试ID
)
@pytest.mark.usefixtures("_setup_account")  # 自动应用指定fixture，等于在每个测试方法的参数列表中添加 _setup_account 参数
class TestEbiddingOnlineSignup:
    @pytest.fixture(autouse=True)
    def _setup_account(self, log, account_session):
        """每个测试方法前的初始化"""
        self.session, self.username, self.company = account_session
        self.current_user_id = None  # 初始化用户ID

    @allure.story('电子标书接口获取')
    @allure.title('供应商访问电子标书列表')
    @pytest.mark.order(1)
    def test_get_bid_list(self, set_host, test_data_cache, log):
        """获取标书列表（依赖用户信息）"""
        user_name = test_data_cache['username']
        available_packs = [p for p in test_data_cache['packages'] if not p['if_signed']]
        # log.warning(f"用户 {user_name}, 可投标包: {[p['pack_no'] for p in available_packs]}")

    @allure.story('电子标书接口获取')
    @allure.title('供应商访问投标人须知')
    @pytest.mark.order(2)
    def test_get_agreeement(self, set_host, account_session, test_data_cache, log):
        """获取投标须知和投标计数（依赖用户信息）"""
        user_id = test_data_cache['user_id']
        agree_id = test_data_cache['agree_id']
        # 获取当前供应商已投计数[不影响流程，可删除]
        test1_url = set_host[set_host['host']] + sign_count_api  # 链式访问，通过host值再解析对应域名
        params1 = {'agreeId': agree_id, "userId": user_id}  # GET请求入参
        account_session[0].get(test1_url, params=params1, headers=headers, verify=False)  # 请求投标人须知
        # log.info(f"当前供应商在本项目已投标包计数：{count_resp.json()}")

    @allure.story('电子标书接口获取')
    @allure.title('供应商提交投标文件获取请求+提交')
    @pytest.mark.order(3)
    def test_sumbit_signup(self, set_host, account_session, test_data_cache, log):
        """获取投标须知和投标计数（依赖用户信息）"""
        # user_id = test_data_cache['user_id']
        # agree_id = test_data_cache['agree_id']
        user_name = test_data_cache['username']
        log.warning(f"test_data_cache: {test_data_cache}")
        available_packs_info = [p for p in test_data_cache['packages'] if not p['if_signed']]
        available_pack_no = gm_extract_json(available_packs_info, '$[*].pack_no')  # 提取可获取电子标书的包
        if not available_pack_no:  #
            log.warning(f"用户 {user_name},在项目{anoteno}无可投标包, 无需获取电子标书")
        else:
            log.warning(f"用户 {user_name}, 在项目{anoteno}中可投标包: {[p['pack_no'] for p in available_packs_info]}，开始获取所有包的电子标书")
            # 拼接获取电子标书payload
            data = {"noticeId": test_data_cache['bidbook_id'],
                    "projectId": test_data_cache['project_id'],
                    "signupWay": 1,
                    "noticeTitle": test_data_cache['content'],
                    "packageNums": available_pack_no,
                    "linkMan": getName(),  # faker库生成模拟数据
                    "linkPhone": getMobile(),  # faker库生成模拟数据
                    "annex": "[]",
                    "procurementProjectId": test_data_cache['item_create_id'],
                    }
            json_data = json.dumps(data)  # 将 data 转换为 JSON 字符串
            test_url = set_host[set_host['host']] + save_signup_api  # 链式访问，通过host值再解析对应域名
            save_singup_resp = account_session[0].post(test_url, data=json_data, headers=headers, verify=False)  # 提交获取电子标书请求
            log.debug(f"提交成功， 报名singup表生成记录，id: {save_singup_resp.json()}")
        log.info(f"{bid_file_list}")
        log.info(f"{bid_file_paths}")

    @allure.story('电子标书接口获取')
    @allure.title('供应商在线投标')
    @pytest.mark.order(4)
    def test_sumbit_bid(self, set_host, account_session, test_data_cache, log):
        """获取投标须知和投标计数（依赖用户信息）"""
        # user_id = test_data_cache['user_id']
        # agree_id = test_data_cache['agree_id']
        user_name = test_data_cache['username']
        # pack_num =


        # === 企业详情 ===
        org_details_url = set_host[set_host['host']] + org_details_api  # 链式访问，通过host值再解析对应域名--企业详情，获取法人等信息
        org_details_resp = account_session[0].get(org_details_url, headers=headers, verify=False)  #
        org_details_json = org_details_resp.json()
        legal_person = gm_extract_json(org_details_json, '$.legalPerson.legalPerson')[0]
        legel_person_identity = gm_extract_json(org_details_json, '$.legalPerson.legalPersonIdentity')[0]
        org_code = gm_extract_json(org_details_json, '$.orgCode')[0]
        org_name = gm_extract_json(org_details_json, '$.orgName')[0]

        # === 在线报名列表 ===
        online_params = {"anoteno": anoteno, "packageNum": '', "current": 1, "size": 100}
        online_singup_info_url = set_host[set_host['host']] + online_signup_info_api
        online_singup_info_resp = account_session[0].get(online_singup_info_url, params=online_params, headers=headers, verify=False)  #
        online_singup_info_json = online_singup_info_resp.json()
        id_package_list = [(item["id"], item['auditState'],  item["packageNum"], item['noticeTitle'],) for item in online_singup_info_json]
        log.info(id_package_list)

        # 标志变量，用于判断是否有符合条件的项
        has_valid_item = False

        for item in id_package_list:
            sign_up_id, audit_state, package_no, notice_title = item
            if audit_state == 3:
                continue

            bid_file_path = find_matching_file(package_no, bid_file_paths)

            # 项目有可投标的包，设置为True
            has_valid_item = True

            #  根据投标文件计算生成md5
            md5_str = calculate_md5(bid_file_path)

            log.info(f"开始报名id={sign_up_id}， 包号={package_no}的投标处理")
            # === 上传前询问 ===
            slice_check_url = set_host[set_host['host']] + slice_check_api  # 链式访问，通过host值再解析对应域名--上传前询问

            slice_check_data = {"md5": md5_str, "sliceCount": 1}
            slice_check_resp = account_session[0].post(slice_check_url, json=slice_check_data, headers=headers, verify=False)  # 上传前询问
            slice_check_json = slice_check_resp.json()
            log.info(f"slice_check_json: {slice_check_json}")
            slice_id = gm_extract_json(slice_check_json, '$.data.sliceIds[0]')[0]

            # === 上传切片 ===
            slice_upload_url = set_host[set_host['host']] + slice_upload_api  # 链式访问，通过host值再解析对应域名--上传切片
            # 准备文件二进制数据
            with open(bid_file_path, "rb") as f:
                file_data = f.read()

            # 准备元数据JSON
            metadata = {
                "compressed": 0,
                "encrypted": 0,
                "business": "ebidding",
                "fileName": bid_file_name,
                "fileSize": os.path.getsize(bid_file_path),
                "md5": md5_str,
                "password": "",
                "sliceCount": 1,
                "sliceId": slice_id,
                "sliceIndex": 0,
                "sliceMd5": md5_str,
            }
            # 关键部分：正确构造files参数
            files = {
                'slice': (bid_file_name, file_data, 'application/octet-stream'),
                'upload': (None, json.dumps(metadata), 'application/json')
            }

            slice_upload_resp = account_session[0].post(slice_upload_url, files=files, headers=form_headers, verify=False)  # 上传切片
            # log.info(f"：{slice_upload_resp.()}")

            # === 询问上传结果 ===
            merge_succeed_url = set_host[set_host['host']] + merge_succeed_api  # 链式访问，通过host值再解析对应域名--询问上传结果
            merge_succeed_params = {"md5": md5_str, "anySliceId": slice_id}
            slice_upload_resp = account_session[0].get(merge_succeed_url, params=merge_succeed_params, headers=headers, verify=False)  # 上传切片
            slice_upload_json = slice_upload_resp.json()
            final_name = gm_extract_json(slice_upload_json, '$.data.finalName')[0]

            # === 上传 ===
            bid_files_url = set_host[set_host['host']] + bid_files_api  # 链式访问，通过host值再解析对应域名--询问上传结果
            bid_files_data = {"value": final_name}
            bid_files_resp = account_session[0].post(bid_files_url, json=bid_files_data, headers=headers, verify=False)  # 上传切片
            bid_files_json = bid_files_resp.json()

            # === 提交投标结果 ===
            try:
                save_bid_url = set_host[set_host['host']] + save_bid_api  # 链式访问，通过host值再解析对应域名--询问上传结果
                save_bid_data = {"bidPersonType": 1,  # 授权代表
                                 "noticeTitle": notice_title,  # 采购项目
                                 "packageNum": package_no,  # 包号
                                 "applicationUnit": org_name,  # 投标单位
                                 "orgCode": org_code,  # 信用代码
                                 "legalPersion": legal_person,  # 法人
                                 "legalPersionIdcard": legel_person_identity,  # 法人身份证
                                 "annex": json.dumps(bid_files_json, ensure_ascii=False),
                                 "linkMan": getName(),  # 授权代表名
                                 "linkPhone": getMobile(),  # 授权代表手机
                                 "verificationCode": "111111",  # 手机验证码，测试环境强制写死
                                 "bidPersonIdcard": getIDcard(),   # 授权代表身份证
                                 "signupId": sign_up_id
                                 }
                save_bid_resp = account_session[0].post(save_bid_url, json=save_bid_data, headers=headers, verify=False)  # 最终提交投标文件
                save_bid_json = save_bid_resp.json()
                if save_bid_resp.status_code != 200:
                    log.error(f"提交投标失败，状态码: {save_bid_resp.status_code}, 响应内容: {save_bid_resp.text}")
                    raise Exception("提交投标失败")
                log.info(f"网上投标最终结果: {save_bid_json}")
            except Exception as e:
                log.error(f"提交投标时发生错误: {e}")
                raise

        # 如果没有找到符合条件的项，打印提示信息
        if not has_valid_item:
            log.warning("所有报名项的状态均为 3，无需处理")
def calculate_md5(file_path):
    """
    计算文件的 MD5 值。

    参数:
        file_path (str): 文件路径。

    返回:
        str: 文件的 MD5 哈希值（十六进制字符串）。
    """
    md5_hash = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):  # 每次读取 4KB 数据
            md5_hash.update(chunk)
    return md5_hash.hexdigest()

def find_matching_file(package_no, bid_file_paths):
    """根据 package_no 找到匹配的文件路径"""
    for file_path in bid_file_paths:
        file_number = extract_package_number_from_filename(file_path)
        if file_number == package_no:
            return file_path
    return None

def extract_package_number_from_filename(file_path):
    """从文件路径中提取 .dat 前的数字"""
    file_name = os.path.basename(file_path)  # 获取文件名
    match = re.search(r"_(\d+)\.dat$", file_name)  # 匹配数字
    return int(match.group(1)) if match else None