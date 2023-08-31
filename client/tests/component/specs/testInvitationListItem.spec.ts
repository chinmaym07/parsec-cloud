// Parsec Cloud (https://parsec.cloud) Copyright (c) BUSL-1.1 2016-present Scille SAS

import InvitationListItem from '@/components/users/InvitationListItem.vue';
import { MockInvitation } from '@/common/mocks';
import { mount } from '@vue/test-utils';
import { DateTime } from 'luxon';
import { mockI18n, getDefaultProvideConfig } from 'tests/component/support/mocks';

mockI18n();

describe('User Invitation List Item', () => {
  it('Display invitation', () => {
    const INVITATION: MockInvitation = {
      token: 'abcdef',
      email: 'dung.eater@lands-between',
      date: DateTime.fromISO('2022-02-25T00:00:00'),
    };

    const wrapper = mount(InvitationListItem, {
      props: {
        invitation: INVITATION,
      },
      global: {
        provide: getDefaultProvideConfig(),
      },
    });

    expect(wrapper.get('.invitation-status').text()).to.equal('UsersPage.invitation.waiting');
    expect(wrapper.get('.invitation-email').text()).to.equal('dung.eater@lands-between');
    const buttons = wrapper.findAll('ion-button');
    expect(buttons.at(0)?.text()).to.equal('UsersPage.invitation.rejectUser');
    expect(buttons.at(1)?.text()).to.equal('UsersPage.invitation.greetUser');

    buttons.at(0)?.trigger('click');
    expect(wrapper.emitted('rejectUser')?.length).to.equal(1);
    expect(wrapper.emitted('rejectUser')?.at(0)?.at(0)).to.deep.equal(INVITATION);
    buttons.at(1)?.trigger('click');
    expect(wrapper.emitted('greetUser')?.length).to.equal(1);
    expect(wrapper.emitted('greetUser')?.at(0)?.at(0)).to.deep.equal(INVITATION);
  });
});