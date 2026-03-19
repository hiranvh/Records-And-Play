import React from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { Layout, Menu, theme } from 'antd';
import {
  PlayCircleOutlined,
  FileTextOutlined,
  SettingOutlined,
  RobotOutlined,
  DesktopOutlined,
  CloudUploadOutlined
} from '@ant-design/icons';
import RecordingDashboard from './components/RecordingDashboard';
import FlowManagement from './components/FlowManagement';
import ConfigurationPanel from './components/ConfigurationPanel';
import AIDataGeneration from './components/AIDataGeneration';
import PlaybackControl from './components/PlaybackControl';
import './App.css';

const { Header, Content, Sider } = Layout;

const App = () => {
  const {
    token: { colorBgContainer, borderRadiusLG },
  } = theme.useToken();

  return (
    <Router>
      <Layout style={{ minHeight: '100vh' }}>
        <Sider collapsible>
          <div className="demo-logo-vertical" />
          <Menu theme="dark" defaultSelectedKeys={['1']} mode="inline">
            <Menu.Item key="1" icon={<DesktopOutlined />}>
              <a href="/">Dashboard</a>
            </Menu.Item>
            <Menu.Item key="2" icon={<PlayCircleOutlined />}>
              <a href="/record">Recording</a>
            </Menu.Item>
            <Menu.Item key="3" icon={<FileTextOutlined />}>
              <a href="/flows">Flow Management</a>
            </Menu.Item>
            <Menu.Item key="4" icon={<PlayCircleOutlined />}>
              <a href="/playback">Playback Control</a>
            </Menu.Item>
            <Menu.Item key="5" icon={<RobotOutlined />}>
              <a href="/ai-data">AI Data Generation</a>
            </Menu.Item>
            <Menu.Item key="6" icon={<SettingOutlined />}>
              <a href="/config">Configuration</a>
            </Menu.Item>
          </Menu>
        </Sider>
        <Layout>
          <Header style={{ padding: 0, background: colorBgContainer }} />
          <Content style={{ margin: '24px 16px 0' }}>
            <div style={{ padding: 24, minHeight: 360, background: colorBgContainer, borderRadius: borderRadiusLG }}>
              <Routes>
                <Route path="/" element={<RecordingDashboard />} />
                <Route path="/record" element={<RecordingDashboard />} />
                <Route path="/record-and-play" element={<RecordingDashboard />} />
                <Route path="/flows" element={<FlowManagement />} />
                <Route path="/playback" element={<PlaybackControl />} />
                <Route path="/ai-data" element={<AIDataGeneration />} />
                <Route path="/config" element={<ConfigurationPanel />} />
              </Routes>
            </div>
          </Content>
        </Layout>
      </Layout>
    </Router>
  );
};

export default App;