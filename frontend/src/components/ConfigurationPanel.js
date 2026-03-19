import React, { useState, useEffect } from 'react';
import {
  Card, Form, Input, Button, Space, message,
  Typography, Divider, Collapse, Switch
} from 'antd';
import { SaveOutlined, SyncOutlined } from '@ant-design/icons';
import axios from 'axios';

const { Title, Text } = Typography;
const { Panel } = Collapse;

const ConfigurationPanel = () => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [config, setConfig] = useState({});

  // Fetch current configuration
  const fetchConfig = async () => {
    setLoading(true);
    try {
      const response = await axios.get('/config');
      setConfig(response.data);
      form.setFieldsValue(response.data);
    } catch (error) {
      message.error('Failed to fetch configuration: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  // Save configuration
  const saveConfig = async (values) => {
    setLoading(true);
    try {
      await axios.put('/config', values);
      message.success('Configuration saved successfully');
      setConfig(values);
    } catch (error) {
      message.error('Failed to save configuration: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  // Test connection
  const testConnection = async (url) => {
    try {
      await axios.post('/config/test-connection', { url });
      message.success('Connection successful');
    } catch (error) {
      message.error('Connection failed: ' + error.message);
    }
  };

  // Initialize
  useEffect(() => {
    fetchConfig();
  }, []);

  return (
    <div>
      <Title level={2}>Configuration</Title>
      <Text type="secondary">Manage your automation framework settings</Text>

      <Divider />

      <Form
        form={form}
        onFinish={saveConfig}
        layout="vertical"
        className="config-panel"
      >
        <Collapse defaultActiveKey={['1', '2', '3', '4', '5', '6']} ghost>
          {/* Application URLs */}
          <Panel header="Application URLs" key="1">
            <Card className="config-section">
              <Title level={4} className="config-section-title">URL Configuration</Title>
              <Form.Item
                name="base.url"
                label="Base URL"
                initialValue={config['base.url'] || 'https://example.com'}
              >
                <Input
                  placeholder="https://example.com"
                  addonAfter={
                    <Button
                      type="link"
                      onClick={() => testConnection(form.getFieldValue('base.url'))}
                    >
                      Test
                    </Button>
                  }
                />
              </Form.Item>

              <Form.Item
                name="login.url"
                label="Login URL"
                initialValue={config['login.url'] || '/login'}
              >
                <Input placeholder="/login" />
              </Form.Item>

              <Form.Item
                name="target.url"
                label="Target URL"
                initialValue={config['target.url'] || '/dashboard'}
              >
                <Input placeholder="/dashboard" />
              </Form.Item>
            </Card>
          </Panel>

          {/* Authentication Credentials */}
          <Panel header="Authentication Credentials" key="2">
            <Card className="config-section">
              <Title level={4} className="config-section-title">Credentials</Title>
              <Form.Item
                name="username"
                label="Username"
                initialValue={config['username'] || 'admin'}
              >
                <Input placeholder="admin" />
              </Form.Item>

              <Form.Item
                name="password"
                label="Password"
                initialValue={config['password'] || 'password123'}
              >
                <Input.Password placeholder="password123" />
              </Form.Item>
            </Card>
          </Panel>

          {/* Standard Test Data */}
          <Panel header="Standard Test Data" key="3">
            <Card className="config-section">
              <Title level={4} className="config-section-title">Test Data Configuration</Title>
              <Form.Item
                name="standard.zipcode"
                label="Zip Code"
                initialValue={config['standard.zipcode'] || '2075'}
              >
                <Input placeholder="2075" />
              </Form.Item>

              <Form.Item
                name="standard.phone"
                label="Phone Number"
                initialValue={config['standard.phone'] || '(555) 123-4567'}
              >
                <Input placeholder="(555) 123-4567" />
              </Form.Item>

              <Form.Item
                name="standard.email.domain"
                label="Email Domain"
                initialValue={config['standard.email.domain'] || 'test.com'}
              >
                <Input placeholder="test.com" />
              </Form.Item>
            </Card>
          </Panel>

          {/* AI Model Settings */}
          <Panel header="AI Model Settings" key="4">
            <Card className="config-section">
              <Title level={4} className="config-section-title">AI Configuration</Title>
              <Form.Item
                name="ai.model.path"
                label="Model Path"
                initialValue={config['ai.model.path'] || './model/phi3_model.bin'}
              >
                <Input placeholder="./model/phi3_model.bin" />
              </Form.Item>

              <Form.Item
                name="ai.temperature"
                label="Temperature"
                initialValue={config['ai.temperature'] || '0.7'}
              >
                <Input placeholder="0.7" type="number" step="0.1" min="0" max="1" />
              </Form.Item>

              <Form.Item
                name="ai.max_tokens"
                label="Max Tokens"
                initialValue={config['ai.max_tokens'] || '500'}
              >
                <Input placeholder="500" type="number" min="1" />
              </Form.Item>
            </Card>
          </Panel>

          {/* Playback Settings */}
          <Panel header="Playback Settings" key="5">
            <Card className="config-section">
              <Title level={4} className="config-section-title">Playback Configuration</Title>
              <Form.Item
                name="playback.speed"
                label="Playback Speed"
                initialValue={config['playback.speed'] || 'normal'}
              >
                <Input placeholder="normal" />
              </Form.Item>

              <Form.Item
                name="playback.retries"
                label="Retry Attempts"
                initialValue={config['playback.retries'] || '3'}
              >
                <Input placeholder="3" type="number" min="0" />
              </Form.Item>

              <Form.Item
                name="playback.timeout"
                label="Timeout (seconds)"
                initialValue={config['playback.timeout'] || '30'}
              >
                <Input placeholder="30" type="number" min="1" />
              </Form.Item>
            </Card>
          </Panel>

          {/* Data Generation Settings */}
          <Panel header="Data Generation Settings" key="6">
            <Card className="config-section">
              <Title level={4} className="config-section-title">Data Generation Configuration</Title>
              <Form.Item
                name="data.dynamic.firstname"
                label="Dynamic First Name"
                valuePropName="checked"
                initialValue={config['data.dynamic.firstname'] === 'true'}
              >
                <Switch checkedChildren="Enabled" unCheckedChildren="Disabled" />
              </Form.Item>

              <Form.Item
                name="data.dynamic.lastname"
                label="Dynamic Last Name"
                valuePropName="checked"
                initialValue={config['data.dynamic.lastname'] === 'true'}
              >
                <Switch checkedChildren="Enabled" unCheckedChildren="Disabled" />
              </Form.Item>

              <Form.Item
                name="data.dynamic.dob"
                label="Dynamic Date of Birth"
                valuePropName="checked"
                initialValue={config['data.dynamic.dob'] === 'true'}
              >
                <Switch checkedChildren="Enabled" unCheckedChildren="Disabled" />
              </Form.Item>

              <Form.Item
                name="data.dynamic.gender"
                label="Dynamic Gender"
                valuePropName="checked"
                initialValue={config['data.dynamic.gender'] === 'true'}
              >
                <Switch checkedChildren="Enabled" unCheckedChildren="Disabled" />
              </Form.Item>

              <Form.Item
                name="data.mask.ssn"
                label="Mask SSN"
                valuePropName="checked"
                initialValue={config['data.mask.ssn'] === 'true'}
              >
                <Switch checkedChildren="Enabled" unCheckedChildren="Disabled" />
              </Form.Item>
            </Card>
          </Panel>
        </Collapse>

        <Form.Item>
          <Space>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              htmlType="submit"
              loading={loading}
            >
              Save Configuration
            </Button>
            <Button
              icon={<SyncOutlined />}
              onClick={fetchConfig}
              loading={loading}
            >
              Reload
            </Button>
          </Space>
        </Form.Item>
      </Form>
    </div>
  );
};

export default ConfigurationPanel;